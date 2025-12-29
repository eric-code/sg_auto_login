import os
import time
import base64
import io
import requests
from datetime import datetime, timedelta
from types import SimpleNamespace
from PIL import Image
from DrissionPage import ChromiumPage
import ddddocr
import random
import threading
import wsproxy
from mqtt_handler import MqttCodeListener
from utils import log, load_config, get_human_tracks


def solve_slider(page):
    log("开始处理滑动验证码...")
    try:
        # 1. 定位元素
        bg_ele = page.ele('css:.verify-img-out img', timeout=5)
        block_ele = page.ele('css:.verify-sub-block img', timeout=5)
        slider_btn = page.ele('css:.verify-move-block', timeout=5)

        if not bg_ele or not block_ele:
            log("未找到验证码图片元素")
            return False

        # 2. 处理图片数据
        def get_bytes(src):
            if ',' in src: src = src.split(',')[1]
            return base64.b64decode(src)

        bg_bytes = get_bytes(bg_ele.attr('src'))
        block_bytes = get_bytes(block_ele.attr('src'))

        # 3. 识别缺口
        ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        res = ocr.slide_match(target_bytes=block_bytes, background_bytes=bg_bytes, simple_target=True)
        target_x = res['target'][0]

        # 4. 计算比例与距离
        render_width = bg_ele.rect.size[0]
        real_width = Image.open(io.BytesIO(bg_bytes)).size[0]
        scale_ratio = render_width / real_width
        final_distance = (target_x * scale_ratio) - 1
        log(f"识别 X: {target_x}, 缩放比: {scale_ratio:.2f}, 最终距离: {final_distance}")

        # 5. 执行模拟滑动
        track_list = get_human_tracks(final_distance)
        page.actions.hold(slider_btn)
        for track in track_list:
            page.actions.move(offset_x=track[0], offset_y=random.choice([0, 0, -1, 1]), duration=0)
            if track[1] > 0: time.sleep(track[1])

        time.sleep(random.uniform(0.2, 0.4))
        page.actions.release()

        target_selector = 'css:.loginselect-dialog > .el-dialog__wrapper'

        # 使用 wait.ele_displayed 等待元素变得可见
        # timeout 设为 3-6 秒比较合适，因为滑动成功后后端返回结果需要一点时间
        is_success = page.wait.ele_displayed(target_selector, timeout=6)

        if is_success:
            log("滑动校验成功：角色选择对话框已显示。")
            return True
        else:
            log("滑动校验失败：角色选择对话框未出现，可能是滑动位置不准。")

            # 失败后通常验证码会自动刷新，如果没有刷新，手动点一下刷新按钮
            try:
                refresh_btn = page.ele('css:.verify-refresh', timeout=1)
                if refresh_btn:
                    log("点击刷新验证码，准备重试...")
                    refresh_btn.click()
                    time.sleep(1)
            except:
                pass

            return False

    except Exception as e:
        log(f"滑动验证码处理异常: {e}")
        return False


def init_browser_and_login(config):
    # 初始化并完成初步登录及滑块验证码
    page = ChromiumPage()
    page.get(config['url'])

    log("输入账号密码...")
    time.sleep(2)
    page.ele('css:input.el-input__inner[placeholder="请输入账号"]').input(config['username'], by_js=False)
    time.sleep(1)
    page.ele('css:input.el-input__inner[placeholder="请输入密码"]').input(config['password'], by_js=False)
    time.sleep(1)
    page.ele('css:button.login-elbutton').click()

    max_retries = 10
    for i in range(max_retries):
        time.sleep(2)
        if page.ele('css:.verify-img-out'):
            log(f"检测到验证码，第 {i+1} 次尝试...")
            if solve_slider(page):
                break # 成功则跳出循环
            else:
                if i == max_retries - 1:
                    log("多次滑动验证失败，放弃")
                    page.quit()
                    return None
                continue
        else:
            log("未检测到验证码，可能已直接进入下一步")
            break

    return page


def handle_verification(page, config, mqtt_listener):
    mode = config.get('verification_mode', 'ukey')
    log(f"当前验证模式: {mode}")
    sms_tab = page.ele('#tab-SMS')
    ukey_tab = page.ele('#tab-USB_KEY')

    if mode == 'ukey':
        # 点击证书验证tab下的验证按钮
        uk_verify_btn = page.ele('css:.ukey_div button')
        uk_verify_btn.click()
        time.sleep(2)

        # 输入 PIN
        uk_input = page.ele('css:input.el-input__inner[placeholder="请输入Ukey口令"]')
        uk_input.input(config['ukey_pin'])

        # 点击确定
        dialog_container = uk_input.parent('css:.el-dialog')
        dialog_container.ele(
            'xpath:.//div[contains(@class, "el-dialog__footer")]//button[contains(., "确 定")]').click()
        log("Ukey 验证表单已提交")

    elif mode == 'sms':
        sms_tab.click()
        time.sleep(1)

        sms_success = False
        max_sms_attempts = 3

        for attempt in range(max_sms_attempts):
            log(f"--- 短信验证尝试 第 {attempt + 1} 次 ---")
            mqtt_listener.clear_code()
            send_code_btn = page.ele(
                'xpath://button[.//span[contains(text(), "获取验证码") or contains(text(), "重新获取") or contains(text(), "s")]]')
            if not send_code_btn:
                log("未找到获取验证码按钮")
                # 找不到来回切换下tab
                ukey_tab.click()
                time.sleep(0.5)
                sms_tab.click()
                time.sleep(3)
                continue

            # 如果按钮还处于禁用状态（例如倒计时还没跑完），则等待
            if 'is-disabled' in send_code_btn.attr('class'):
                log("按钮仍在倒计时/禁用状态，等待恢复...")
                # 动态等待按钮文本恢复为“重新获取”或“获取验证码”，最多等 10 秒（因为 MQTT 已经等了 80 秒）
                # wait.ele_displayed 会检测元素是否可见/可用
                is_ready = page.wait.ele_displayed(
                    'xpath://button[not(contains(@class, "is-disabled")) and .//span[contains(text(), "重新获取")]]',
                    timeout=10)
                if not is_ready:
                    log("按钮恢复超时，尝试来回切换tab")
                    ukey_tab.click()
                    time.sleep(0.5)
                    sms_tab.click()
                    time.sleep(70)
                    continue

            log(f"点击发送验证码: {send_code_btn.text.strip()}")
            send_code_btn.click()

            # 阻塞等待 MQTT 验证码 (80秒)
            code = mqtt_listener.get_code(timeout=80)

            if code:
                log(f"收到验证码: {code}")

                # 逻辑：按钮 -> 父div -> 该div的兄弟节点中的input（且placeholder为短信验证码）
                # 我们先跳到父div，再跳到共同的父容器，然后查找符合条件的input
                input_ele = send_code_btn.parent('tag:div').parent().ele('css:input[placeholder="短信验证码"]')

                if input_ele:
                    log("成功定位到短信验证码输入框")
                    # 确保元素可见并点击
                    input_ele.click()
                    # 先用 JS 清空，再模拟输入
                    input_ele.run_js('this.value=""')
                    time.sleep(0.5)
                    # 模拟真实输入
                    input_ele.input(code, by_js=False)
                    log(f"已填入验证码: {code}")
                    time.sleep(1)

                    # 点击“验证”按钮（注意按钮文本可能有空格）
                    confirm_btn = page.ele('xpath://button[.//span[contains(text(), "验 证")]]')
                    if confirm_btn:
                        confirm_btn.click()

                    # 验证是否登录成功
                    try:
                        # 等待 URL 变化，出现 dashboard 即为成功
                        if page.wait.url_change(text='dashboard', timeout=10):
                            log("短信验证成功，已进入系统")
                            sms_success = True
                            break
                        else:
                            log(f"登录报错,等待70秒后再试")
                            time.sleep(70)
                    except:
                        log("输入验证码后跳转超时")
                else:
                    log("未能通过相对路径定位到输入框")

            else:
                log(f"第 {attempt + 1} 次尝试：80秒内未收到 MQTT 消息")
                # 如果这是最后一次尝试，且没收到，流程就结束了
                if attempt < max_sms_attempts - 1:
                    log("准备进行下一次重新发送...")
                    # 这里不需要 sleep 太多，因为 loop 开始会重新检查按钮状态

        return sms_success

    else:
        log("无需额外验证或未知模式")
        return True

def process_cookies_and_keep_alive(page, config):
    """步骤 7：获取 Cookie 并进行保活"""
    remote_server_url = config.get('push_server_url')

    # 1. 等待登录成功跳转
    try:
        log("等待跳转到 dashboard...")
        page.wait.url_change(text='dashboard', timeout=15)
        page.wait.load_start()
    except:
        log("等待跳转超时，尝试获取当前状态")

    # 2. 初始 Cookie 获取与发送
    def get_and_push_cookies():
        cookies_list = page.cookies()
        cookies_dict = {item['name']: item['value'] for item in cookies_list}
        if cookies_dict and remote_server_url:
            data = SimpleNamespace(cookies=cookies_dict, payload={"username": config['username']})
            send_cookies_to_server(data, remote_server_url)
        return cookies_dict

    get_and_push_cookies()

    # 3. 保活循环
    keep_alive_h = config.get('keep_alive_duration_hours', 2)
    interval_m = config.get('keep_alive_interval_minutes', 10)
    end_time = datetime.now() + timedelta(hours=keep_alive_h)
    log(f"开始保活，预计结束时间: {end_time.strftime('%H:%M:%S')}")

    try:
        while datetime.now() < end_time:
            # 计算下次刷新等待时间（带抖动）
            sleep_sec = (interval_m * 60) + random.uniform(-60, 60)
            log(f"等待 {sleep_sec:.1f} 秒后进行下次刷新...")
            time.sleep(max(sleep_sec, 5))

            log("执行页面刷新保活...")
            page.refresh()
            time.sleep(random.uniform(2, 5))

            if 'dashboard' not in page.url:
                log(f"检测到已掉线 (URL: {page.url})")
                break

            # 刷新后更新并重新发送 Cookie
            get_and_push_cookies()

    except Exception as e:
        log(f"保活异常: {e}")
    finally:
        log("保活结束，关闭浏览器")
        page.quit()

def send_cookies_to_server(data, server_url):
    """
    将 Cookie 发送到远程服务器
    """
    try:
        # ---------------------------------------------------------
        # 1. 数据处理：将字典/列表转换为 "key=value; key=value" 字符串
        # ---------------------------------------------------------

        # 如果 page_cookies 是列表 (as_dict=False)，先转成字典便于处理
        if isinstance(data.cookies, list):
            cookie_dict = {item['name']: item['value'] for item in data.cookies}
        else:
            cookie_dict = data.cookies
        cookie_string = "; ".join([f"{key}={value}" for key, value in cookie_dict.items()])

        # ---------------------------------------------------------
        # 2. 构造请求：
        # ---------------------------------------------------------
        headers = {
            "xcookie": cookie_string,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # 获取 CA 根证书的路径
        ca_path = os.path.join("certs", "ca.crt")

        log(f"正在发送 Cookie 到服务器: {server_url}")
        # 发送 POST 请求
        response = requests.post(
            server_url,
            headers=headers,
            json=data.payload,
            timeout=10,
            verify=ca_path)
        if response.status_code == 200:
            res_json = response.json()
            log("接口响应成功: " + str(res_json))
        else:
            log(f"接口报错，状态码: {response.status_code}, 内容: {response.text}")

    except Exception as e:
        log(f"发送请求时出错: {e}")

def auto_login(config, mqtt_listener=None):
    try:
        page = init_browser_and_login(config)
        if not page: return

        verify_res = handle_verification(page, config, mqtt_listener)
        if not verify_res:
            log("二次验证失败，流程终止")
            return

        process_cookies_and_keep_alive(page, config)
    except Exception as e:
        log(f"流程执行异常: {e}")


if __name__ == '__main__':
    current_config = load_config()
    if current_config:
        default_settings = {
            'mqtt_username': 'sgsms',
            'mqtt_password': '4$90*xyP$nqNocP',
            'mqtt_topic': 'sms/verification',
            'mqtt_host': '58.220.240.50',
            'mqtt_port': 17181,
            'mqtt_qos':2
        }
        current_config = {**default_settings, **current_config}
    else:
        log("配置读取失败")
        exit()

    mqtt_service = None
    if current_config.get('verification_mode') == 'sms':
        log("正在启动 MQTT 验证码监听服务...")
        mqtt_service = MqttCodeListener(current_config)
        mqtt_service.start()

    if current_config.get('enable_local_proxy', False):
        log("配置为开启：正在启动本地 Ukey 转发代理...")
        proxy_thread = threading.Thread(target=wsproxy.run_proxy_server, daemon=True)
        proxy_thread.start()
        # 稍微等待一下让端口监听启动
        time.sleep(1)
    else:
        log("配置为关闭：跳过启动本地 Ukey 转发代理")

    try:
        auto_login(current_config, mqtt_listener=mqtt_service)
    except Exception as e:
        log(f"程序运行出错: {e}")
    finally:
        # 释放资源
        if mqtt_service:
            mqtt_service.stop()
            log("MQTT 服务已关闭")