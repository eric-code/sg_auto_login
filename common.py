import ipaddress
import os
import sys
import json
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.hazmat._oid import NameOID
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