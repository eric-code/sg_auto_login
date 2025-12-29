import os
import ipaddress
from datetime import datetime, timedelta
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# ================= 配置区 =================
CERT_DIR = "certs"  # 存放 ca.key 和 ca.crt 的目录
TARGET_IP = "58.220.240.50"  # 远程服务器 IP
OUT_CRT = "remote_server.crt"  # 输出的服务器证书名
OUT_KEY = "remote_server.key"  # 输出的服务器私钥名


# ==========================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def generate_remote_cert():
    # 1. 路径检查
    ca_key_path = os.path.join(CERT_DIR, "ca.key")
    ca_cert_path = os.path.join(CERT_DIR, "ca.crt")

    if not os.path.exists(ca_key_path) or not os.path.exists(ca_cert_path):
        log(f"错误: 在 {CERT_DIR} 目录下找不到 ca.key 或 ca.crt")
        log("请确保你已经运行过主程序的初始化逻辑并生成了根证书。")
        return

    try:
        log(f"正在读取根证书并为 {TARGET_IP} 签发专用证书...")

        # 2. 加载现有的 CA 根证书和私钥
        with open(ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(ca_cert_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())

        # 3. 生成远程服务器私钥 (ECC SECP256R1)
        server_key = ec.generate_private_key(ec.SECP256R1())

        # 4. 构造服务器身份信息 (CN 必须匹配 IP)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, TARGET_IP),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"CertAide"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, u"RemoteNode"),
        ])

        # 5. 构造 SAN (Subject Alternative Name) - 解决现代浏览器/Requests报错的关键
        alt_names = x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.IPv4Address(TARGET_IP))
        ])

        # 6. 签发证书
        builder = x509.CertificateBuilder()
        builder = builder.subject_name(subject)
        builder = builder.issuer_name(ca_cert.subject)  # 继承根证书的 Subject
        builder = builder.public_key(server_key.public_key())
        builder = builder.serial_number(x509.random_serial_number())
        builder = builder.not_valid_before(datetime.utcnow() - timedelta(days=1))
        builder = builder.not_valid_after(datetime.utcnow() + timedelta(days=3650))  # 10年有效期
        builder = builder.add_extension(alt_names, critical=False)

        # 使用 CA 的私钥进行签名
        server_cert = builder.sign(ca_key, hashes.SHA256())

        # 7. 保存结果
        with open(os.path.join(CERT_DIR, OUT_KEY), "wb") as f:
            f.write(server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        with open(os.path.join(CERT_DIR, OUT_CRT), "wb") as f:
            f.write(server_cert.public_bytes(serialization.Encoding.PEM))

        log("=" * 40)
        log("签发成功！")
        log(f"证书文件: {os.path.join(CERT_DIR, OUT_CRT)}")
        log(f"私钥文件: {os.path.join(CERT_DIR, OUT_KEY)}")

    except Exception as e:
        log(f"发生异常: {e}")


if __name__ == "__main__":
    generate_remote_cert()