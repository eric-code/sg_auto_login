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
        final_distance = (target_x * scale_ratio) - 2
        log(f"识别 X: {target_x}, 缩放比: {scale_ratio:.2f}, 最终距离: {final_distance}")

        # 5. 执行模拟滑动
        track_list = get_human_tracks(final_distance)
        page.actions.hold(slider_btn)
        for track in track_list:
            page.actions.move(offset_x=track[0], offset_y=random.choice([0, 0, -1, 1]), duration=0)
            if track[1] > 0: time.sleep(track[1])

        time.sleep(random.uniform(0.2, 0.4))
        page.actions.release()
        return True
    except Exception as e:
        log(f"滑动验证码处理异常: {e}")
        return False


def init_browser_and_login(config):
    """步骤 1-5：初始化并完成初步登录及验证码"""
    page = ChromiumPage()
    page.get(config['url'])

    log("输入账号密码...")
    time.sleep(2)
    page.ele('css:input.el-input__inner[placeholder="请输入账号"]').input(config['username'])
    time.sleep(1)
    page.ele('css:input.el-input__inner[placeholder="请输入密码"]').input(config['password'])
    time.sleep(1)
    page.ele('css:button.login-elbutton').click()

    # 等待验证码出现并处理
    time.sleep(3)
    if page.ele('css:.verify-img-out'):
        success = solve_slider(page)
        if not success:
            log("滑动验证失败")
            return None

    time.sleep(2)
    return page


def handle_verification(page, config, mqtt_listener):
    """步骤 6：根据模式进行二次验证"""
    mode = config.get('verification_mode', 'ukey')
    log(f"当前验证模式: {mode}")

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
        # 切换到短信验证tab
        page.ele('#tab-SMS').click()
        time.sleep(1)
        page.ele('xpath://button//span[contains(text(), "获取验证码")]').click()

        # 阻塞等待验证码到达
        code = mqtt_listener.get_code(timeout=60)
        if code:
            log(f"从 MQTT 拿到验证码: {code}，正在输入...")
            # 找到验证码输入框并输入
            input_ele = page.ele('css:input.el-input__inner[placeholder="短信验证码"]')
            input_ele.input(code)
            time.sleep(1)
            # 点击登录或确认
            page.ele('xpath://button//span[contains(text(), "验 证")]').click()
        else:
            log("超时未获取到 MQTT 验证码")

    else:
        log("无需额外验证或未知模式")


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

        log(f"正在发送 Cookie 到服务器: {server_url}")
        # 发送 POST 请求
        response = requests.post(server_url, headers=headers, json=data.payload, timeout=10)
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

        handle_verification(page, config, mqtt_listener)

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