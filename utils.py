import ipaddress
import os
import random
import sys
import subprocess
import json
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def get_base_path():
    """获取基础路径"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def log(content):
    """公用日志方法"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{timestamp}] {content}"
    print(msg)
    # 这里的 run_log.txt 路径也建议用 get_base_path 拼接，防止路径错乱
    log_path = os.path.join(get_base_path(), 'run_log.txt')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(msg + "\n")

def load_config():
    """公用配置读取"""
    config_path = os.path.join(get_base_path(), 'config.json')
    if not os.path.exists(config_path):
        log(f"错误：找不到配置文件 {config_path}")
        return None
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_self_signed_cert(cert_dir="certs"):
    """
    生成高度伪装的 ECC 证书，模仿 pawdroot / CertAide
    """
    if not os.path.exists(cert_dir):
        os.makedirs(cert_dir)

    ca_key_path = os.path.join(cert_dir, "ca.key")
    ca_cert_path = os.path.join(cert_dir, "ca.crt")
    server_key_path = os.path.join(cert_dir, "server.key")
    server_cert_path = os.path.join(cert_dir, "server.crt")

    # 如果文件已存在，直接返回（如果想强制更新，请手动删除 certs 文件夹）
    if os.path.exists(server_cert_path) and os.path.exists(server_key_path):
        return ca_cert_path, server_cert_path, server_key_path

    log("正在生成伪装证书 (ECC-256)...")

    # =========================================================================
    # 1. 生成 CA 证书 (模仿 pawdroot)
    # =========================================================================

    # 使用 ECC 生成 CA 私钥 (原版通常根证书也是ECC，或者RSA，这里统一用ECC)
    ca_key = ec.generate_private_key(ec.SECP256R1())

    # 构造 CA 的身份信息 (完全照抄截图)
    ca_subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"pawdroot"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"CertAide"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, u"pawd"),
    ])

    ca_cert = x509.CertificateBuilder().subject_name(ca_subject).issuer_name(ca_subject).public_key(
        ca_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.utcnow()
    ).not_valid_after(
        # 截图是1年，这里设置为 3650 天(10年)是为了省事，防止明年过期，
        # 如果追求极致逼真，可以改成 timedelta(days=365)
        datetime.utcnow() + timedelta(days=3650)
    ).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), critical=True,
    ).sign(ca_key, hashes.SHA256())

    # 保存 CA 私钥和证书
    with open(ca_key_path, "wb") as f:
        f.write(ca_key.private_bytes(encoding=serialization.Encoding.PEM,
                                     format=serialization.PrivateFormat.TraditionalOpenSSL,
                                     encryption_algorithm=serialization.NoEncryption()))
    with open(ca_cert_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    # =========================================================================
    # 2. 生成 Server 证书 (模仿 127.0.0.1)
    # =========================================================================

    # 使用 ECC 生成 Server 私钥
    server_key = ec.generate_private_key(ec.SECP256R1())

    # 构造 Server 的身份信息 (完全照抄截图)
    server_subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"127.0.0.1"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"CertAide"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, u"pawd"),
    ])

    # 添加 IP SAN (这对于 Chrome 是必须的)
    alt_names = [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]

    server_cert = x509.CertificateBuilder().subject_name(server_subject).issuer_name(ca_subject).public_key(
        server_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.utcnow()
    ).not_valid_after(
        datetime.utcnow() + timedelta(days=3650)
    ).add_extension(
        x509.SubjectAlternativeName(alt_names), critical=False,
    ).sign(ca_key, hashes.SHA256())

    # 保存 Server 私钥和证书
    with open(server_key_path, "wb") as f:
        f.write(server_key.private_bytes(encoding=serialization.Encoding.PEM,
                                         format=serialization.PrivateFormat.TraditionalOpenSSL,
                                         encryption_algorithm=serialization.NoEncryption()))
    with open(server_cert_path, "wb") as f:
        f.write(server_cert.public_bytes(serialization.Encoding.PEM))

    log(f"证书生成完毕。")
    log(f"请务必在机器A上安装生成的 CA 证书: {os.path.abspath(ca_cert_path)}")
    log(f"安装位置: 受信任的根证书颁发机构")

    return ca_cert_path, server_cert_path, server_key_path

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


def install_cert_to_windows(cert_path):
    """
    将证书安装到 Windows '当前用户' 的 '受信任的根证书颁发机构'
    :param cert_path: 证书文件的绝对路径或相对路径
    :return: bool 是否执行成功
    """
    # 获取绝对路径，防止命令行找不到文件
    abs_cert_path = os.path.abspath(cert_path)

    if not os.path.exists(abs_cert_path):
        print(f"错误: 找不到证书文件: {abs_cert_path}")
        return False

    log(f"正在尝试安装证书: {abs_cert_path} ...")
    log("注意: 系统将弹出一个安全警告窗口，请点击【是(Y)】以继续安装。")

    try:
        # 使用 Windows 自带的 certutil 工具
        # -addstore: 添加证书
        # -user: 安装到当前用户（不需要管理员权限，但会有弹窗）
        # Root: 指定存储区为"受信任的根证书颁发机构"
        command = ["certutil", "-addstore", "-user", "Root", abs_cert_path]

        # 隐藏命令行窗口（可选，防止黑框一闪而过）
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # 执行命令
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='gbk',  # Windows 中文环境通常是 gbk
            startupinfo=startupinfo
        )

        if result.returncode == 0:
            log("证书安装命令执行成功（请确认你点击了弹窗的'是'）。")
            return True
        else:
            log(f"安装失败: {result.stdout} {result.stderr}")
            return False

    except Exception as e:
        log(f"发生异常: {e}")
        return False


def check_and_install_cert(cert_base_path):
    cert_path = os.path.join(cert_base_path, "ca.crt")
    flag_path = os.path.join(cert_base_path, "cert_installed.flag")

    # 如果标记文件存在，假设用户已经安装过了
    if os.path.exists(flag_path):
        return

    # 执行安装
    success = install_cert_to_windows(cert_path)

    # 如果安装命令成功（用户点没点'是'检测不到，只能假设他点了），写入标记
    if success:
        with open(flag_path, "w") as f:
            f.write("installed")