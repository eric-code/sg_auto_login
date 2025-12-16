import time
import base64
import io
import sys
import os
import json
import requests
from datetime import datetime, timedelta
from types import SimpleNamespace
from PIL import Image
from DrissionPage import ChromiumPage
import ddddocr
import random

def get_base_path():
    """
    获取程序运行的基础路径：
    1. 如果是打包后的 exe，返回 exe 所在目录
    2. 如果是 py 脚本，返回脚本所在目录
    """
    if getattr(sys, 'frozen', False):
        # 处于 exe 运行模式
        return os.path.dirname(sys.executable)
    else:
        # 处于 py 脚本运行模式
        return os.path.dirname(os.path.abspath(__file__))

def log(content):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{timestamp}] {content}"
    print(msg)
    with open('run_log.txt', 'a', encoding='utf-8') as f:
        f.write(msg + "\n")

def load_config():
    # 拼接出 config.json 的绝对路径
    config_path = os.path.join(get_base_path(), 'config.json')

    if not os.path.exists(config_path):
        log(f"错误：找不到配置文件 {config_path}")
        return None

    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_human_tracks(distance):
    """
    生成高速拟人轨迹
    :param distance: 总距离
    :return: 轨迹列表 [[dx, dy, sleep_time], ...]
    """
    tracks = []
    current = 0
    # 减速阈值：滑到 85% 的距离开始减速
    mid = distance * 0.85
    t = 0.2  # 时间计算单位
    v = 0  # 初始速度

    # 故意多滑一点 (过冲 3-8 px)
    target = distance + random.randint(3, 8)

    while current < target:
        if current < mid:
            # 提升加速度,不然会滑得很慢，让起步和中间段非常快
            a = random.randint(7, 17)
        else:
            # 减速阶段，急速刹车
            a = -random.randint(12, 23)

        v0 = v
        v = v0 + a * t
        move = v0 * t + 0.5 * a * t * t

        # 即使减速，最低也保持 2px 的移动，防止最后阶段太磨叽
        if move < 2: move = 2

        current += move

        # 将 sleep 时间放入轨迹数据中
        # 如果是加速阶段（中间），几乎不等待；如果是减速阶段（结尾），稍微带点延迟
        if current < mid:
            sleep_t = 0  # 高速段不睡觉
        else:
            sleep_t = random.uniform(0.001, 0.005)  # 结尾微小延迟

        tracks.append([round(move), sleep_t])

    # --- 回退修正 (回拉) ---
    back_tracks = []
    back_distance = current - distance

    # 回退时步子也不要太小，防止磨蹭
    while back_distance > 0:
        if back_distance > 5:
            move = random.randint(3, 5)  # 距离远就拉快点
        else:
            move = random.randint(1, 2)  # 距离近就微调

        if back_distance < move:
            move = back_distance

        back_tracks.append([-move, random.uniform(0.01, 0.02)])
        back_distance -= move

    return tracks + back_tracks


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


def auto_login():
    # 1. 初始化浏览器
    page = ChromiumPage()

    config = load_config()
    target_url = config['url']
    username = config['username']
    password = config['password']
    ukey_pin = config['ukey_pin']

    page.get(target_url)

    # --- 模拟登录操作  ---
    time.sleep(2)
    page.ele('css:input.el-input__inner[placeholder="请输入账号"]').input(username)
    time.sleep(2)
    page.ele('css:input.el-input__inner[placeholder="请输入密码"]').input(password)
    time.sleep(1)
    btn = page.ele('css:button.login-elbutton')
    btn.click()

    # 等待验证码弹窗出现
    print("等待验证码加载...")
    # AJCaptcha 通常的包裹容器类名是 .verify-box 或类似的
    # 如果是点击后才弹出的，这里需要确保它已经显示
    time.sleep(3)

    # --- 2. 获取验证码图片 ---
    # AJCaptcha 标准结构中：
    # 背景图类名通常包含 verify-img-out 或 verify-img-panel
    # 滑块图类名通常包含 verify-sub-block

    try:
        # 获取背景图片元素
        bg_ele = page.ele('css:.verify-img-out img')
        # 获取滑块图片元素
        # 注意：AJCaptcha 有时候滑块是单独的img，有时候是canvas，这里假设是img或div带背景
        # 如果是 canvas，获取方式会略有不同
        block_ele = page.ele('css:.verify-sub-block img')

        # 如果找不到，打印一下页面源码排查
        if not bg_ele or not block_ele:
            log("未找到图片元素，可能是加载延迟或选择器错误")
            return

        # 获取滑块按钮（用于拖拽的那个按钮）
        slider_btn = page.ele('css:.verify-move-block')
    except:
        log("未找到验证码元素，请检查选择器")
        return

    # 获取图片的 src 属性 (通常是 data:image/png;base64,...)
    bg_src = bg_ele.attr('src')
    block_src = block_ele.attr('src')

    # 处理 Base64 数据
    def save_base64_image(data_str):
        # 去掉 'data:image/png;base64,' 前缀
        if ',' in data_str:
            data_str = data_str.split(',')[1]
        return base64.b64decode(data_str)

    bg_bytes = save_base64_image(bg_src)
    block_bytes = save_base64_image(block_src)

    # --- 3. 识别缺口位置 ---
    ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

    # slide_match 返回结构: {'target': [x, y, w, h], 'bg': [w, h]}
    res = ocr.slide_match(target_bytes=block_bytes, background_bytes=bg_bytes, simple_target=True)

    target_x = res['target'][0]
    log(f"识别到的缺口原始坐标 X: {target_x}")

    # --- 4. 处理缩放比例 (关键步骤) ---
    # 网页上图片显示的宽度
    render_width = bg_ele.rect.size[0]
    # 实际图片的宽度 (ddddocr 告诉我们的，或者用 PIL 读取)
    img = Image.open(io.BytesIO(bg_bytes))
    real_width = img.size[0]  # img.size 返回 (width, height)

    scale_ratio = render_width / real_width
    log(f"网页渲染宽度: {render_width}, 图片原始宽度: {real_width}, 缩放比例: {scale_ratio}")

    # 计算实际需要滑动的距离
    final_distance = target_x * scale_ratio

    # 修正：AJcaptcha 有时候滑块初始位置不在 0，或者有边框偏移
    # 这里的 5 是经验值，可能需要根据具体网站微调（例如减去滑块的一半宽度等，AJ通常不需要）
    final_distance = final_distance - 2

    log(f"最终计划滑动距离: {final_distance}")

    # --- 5. 执行滑动 ---
    # --- 执行拟人滑动 ---
    # 生成优化后的轨迹
    track_list = get_human_tracks(final_distance)
    log(f"轨迹点数量: {len(track_list)} (步数越少越快)")
    page.actions.hold(slider_btn)
    # 开始移动
    for track in track_list:
        dx = track[0]  # X轴移动距离
        sleep_t = track[1]  # 等待时间
        # Y轴微小抖动：大部分时候不动(0)，偶尔抖一下
        dy = random.choice([0, 0, -1, 1])
        # duration=0 表示 DrissionPage 内部不等待，全速发送指令
        page.actions.move(offset_x=dx, offset_y=dy, duration=0)
        # 只有在需要的时候才 sleep (主要是结尾阶段)
        if sleep_t > 0:
            time.sleep(sleep_t)
    # 模拟松手前的最后确认（这个时间不能省，防风控关键）
    time.sleep(random.uniform(0.2, 0.4))
    page.actions.release()

    time.sleep(2)

    # --- 6. 点击证书验证tab下的验证按钮 ---
    uk_verify_btn = page.ele('css:.ukey_div button')
    uk_verify_btn.click()
    time.sleep(2)

    uk_input = page.ele('css:input.el-input__inner[placeholder="请输入Ukey口令"]')
    uk_input.input(ukey_pin)

    dialog_container = uk_input.parent('css:.el-dialog')
    # 含义：在当前节点(.)内部，找 class 包含 footer 的 div，下面包含文字的 button
    dialog_container.ele('xpath:.//div[contains(@class, "el-dialog__footer")]//button[contains(., "确 定")]').click()


    remote_server_url = config.get('push_server_url')
    # 1. 等待 URL 发生变化目前是出现dashboard (判断登录成功的关键)
    try:
        page.wait.url_change(text='dashboard', timeout=15)
        # 等待页面加载完毕
        page.wait.load_start()
    except:
        log("等待跳转超时，尝试直接获取 Cookie")

    # 获取 cookie 列表对象
    cookies_list = page.cookies()
    # 转为字典格式: {'JSESSIONID': 'xxx', 'uid': 'xxx'}
    cookies_dict = {item['name']: item['value'] for item in cookies_list}

    data = SimpleNamespace()
    data.cookies = cookies_dict
    data.payload = {
        "username": username
    }

    # --- 保活逻辑开始 ---
    # 获取保活时长，默认为 2 小时
    keep_alive_duration_hours = config.get('keep_alive_duration_hours', 2)
    # 获取刷新间隔，默认为 10 分钟
    keep_alive_interval_minutes = config.get('keep_alive_interval_minutes', 10)
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=keep_alive_duration_hours)
    log(f"浏览器将保持活跃至: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 将刷新间隔转换为秒
    sleep_seconds = keep_alive_interval_minutes * 60

    try:
        while datetime.now() < end_time:
            log(f"正在进行保活刷新... 剩余时长: {(end_time - datetime.now()).total_seconds() / 60:.1f} 分钟")

            # 执行页面刷新
            page.refresh()
            time.sleep(random.uniform(2, 5))  # 刷新后稍微等待，模拟加载

            # 如果当前 URL 中不包含 'dashboard'，则认为已掉线
            if 'dashboard' not in page.url:
                log(f"检测到掉线(当前URL: {page.url})，提前结束保活。")
                break

            # 重新获取最新的 Cookie 并发送，因为有些网站会更新 Session ID
            current_cookies_list = page.cookies()
            current_cookies_dict = {item['name']: item['value'] for item in current_cookies_list}

            # 更新 data 对象中的 cookie
            data.cookies = current_cookies_dict

            # 在循环内发送请求（现在这是第一次发送）
            if current_cookies_dict and remote_server_url:
                send_cookies_to_server(data, remote_server_url)

            # 等待下一个刷新周期
            time.sleep(sleep_seconds)

    except Exception as e:
        log(f"保活期间发生错误: {e}")

    finally:
        log(f"保活 {keep_alive_duration_hours} 小时已结束，或遇到错误/掉线。正在关闭浏览器。")
        # 关闭浏览器
        page.quit()

if __name__ == '__main__':
    auto_login()