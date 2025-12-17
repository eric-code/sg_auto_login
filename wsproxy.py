import asyncio
import ssl
import os
import sys
from utils import log, get_base_path, load_config, generate_self_signed_cert, check_and_install_cert


def log_proxy(msg):
    # 简单的日志输出，为了不和主程序混淆，加个前缀
    log(f"[Proxy] {msg}")


def decode_ws_payload(data):
    """
    尝试解析 WebSocket 帧并提取 Payload 文本
    仅用于日志打印，不严谨，但能处理大部分短报文
    """
    try:
        if len(data) < 2:
            return None

        # Byte 0: FIN + Opcode
        # Byte 1: Mask + Payload Len
        second_byte = data[1]

        # 检查 Mask 位 (第8位)
        is_masked = (second_byte & 0x80) >> 7
        payload_len = second_byte & 0x7F

        payload_start = 2

        # 处理长度字段
        if payload_len == 126:
            payload_start = 4  # 2 header + 2 length
        elif payload_len == 127:
            payload_start = 10  # 2 header + 8 length

        if len(data) < payload_start:
            return None

        # 提取 Mask Key
        mask_key = None
        if is_masked:
            if len(data) < payload_start + 4:
                return None
            mask_key = data[payload_start: payload_start + 4]
            payload_start += 4

        # 提取 Payload 数据
        payload = data[payload_start:]

        # 如果有掩码，需要进行异或解密 (XOR unmasking)
        if is_masked:
            unmasked_payload = bytearray()
            for i in range(len(payload)):
                unmasked_payload.append(payload[i] ^ mask_key[i % 4])
            payload = unmasked_payload

        # 尝试转为 UTF-8 文本
        return payload.decode('utf-8')

    except Exception:
        # 解析失败（可能是分包了，或者是纯二进制帧）
        return None


async def pipe(reader, writer, direction_label):
    """
    将数据从 reader 管道转发到 writer
    """
    try:
        while True:
            # 读取数据
            data = await reader.read(4096)
            if not data:
                break

            # --- 智能日志记录 ---
            if len(data) > 0:
                # 尝试解析 WebSocket 协议
                ws_text = decode_ws_payload(data)

                if ws_text:
                    # 如果解析成功，打印干净的文本
                    # 去掉换行符方便单行显示
                    clean_text = ws_text.strip().replace('\n', ' ')
                    # 限制日志长度，防止 JSON 太长刷屏，保留前 500 字符
                    # if len(clean_text) > 500:
                    #     clean_text = clean_text[:500] + "..."
                    log_proxy(f"[{direction_label}] (WS-Decoded): {clean_text}")
                else:
                    # 解析失败（非文本帧或分片），回退到原始打印
                    log_proxy(f"[{direction_label}] {len(data)} bytes (Raw/Binary)")

            # --- 转发数据 (原封不动) ---
            writer.write(data)
            await writer.drain()

    except Exception as e:
        log_proxy(f"Pipe error {direction_label}: {e}")
    finally:
        try:
            writer.close()
        except:
            pass


async def handle_client(client_reader, client_writer, target_ip, target_port, target_ssl_ctx):
    """
    处理每一个来自浏览器的连接
    """
    try:
        log_proxy("New browser connection received.")

        # 连接到远程机器 B
        # 注意：机器 B 上的 Ukey 也是 SSL 服务，所以需要 SSL 连接
        # server_hostname=None 和 check_hostname=False 极其重要，因为我们在连 IP，且可能不验证 B 的证书
        try:
            remote_reader, remote_writer = await asyncio.open_connection(
                target_ip, target_port, ssl=target_ssl_ctx, server_hostname=None
            )
        except Exception as e:
            log_proxy(f"无法连接到Ukey主机 ({target_ip}:{target_port}) : {e}")
            return

        # 创建双向管道
        task1 = asyncio.create_task(pipe(client_reader, remote_writer, "本机->Ukey主机"))
        task2 = asyncio.create_task(pipe(remote_reader, client_writer, "Ukey主机->本机"))

        done, pending = await asyncio.wait([task1, task2], return_when=asyncio.FIRST_COMPLETED)

        # 取消剩余的任务
        for task in pending:
            task.cancel()

    except Exception as e:
        log_proxy(f"Connection handler error: {e}")
    finally:
        try:
            client_writer.close()
        except:
            pass
        log_proxy("Connection closed.")


async def start_server_async(local_port, target_ip, target_port):
    base_path = get_base_path()

    # 1. 准备证书
    cert_dir = os.path.join(base_path, 'certs')
    try:
        # 自动检查是否存在，不存在则生成
        ca_path, cert_path, key_path = generate_self_signed_cert(cert_dir)
        # 检查是否安装过，没安装过就安装证书
        check_and_install_cert(cert_dir)

    except Exception as e:
        log_proxy(f"证书获取失败: {e}")
        return

    # 2. 配置 A 机器监听的 SSL 上下文 (Server 端 - 欺骗浏览器用)
    server_ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

    # 3. 配置连接 B 机器的 SSL 上下文 (Client 端 - 连接真实Ukey服务用)
    client_ssl_ctx = ssl.create_default_context()
    client_ssl_ctx.check_hostname = False
    client_ssl_ctx.verify_mode = ssl.CERT_NONE

    # 4. 启动监听
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, target_ip, target_port, client_ssl_ctx),
        '0.0.0.0', local_port, ssl=server_ssl_ctx
    )

    log_proxy(f"Listening on 127.0.0.1:{local_port} (SSL) -> Forwarding to {target_ip}:{target_port}")

    async with server:
        await server.serve_forever()


def run_proxy_server():
    # 读取配置
    config = load_config()
    if not config:
        return

    target_ip = config.get('ukey_proxy_target_ip')
    target_port = config.get('ukey_proxy_target_port')
    local_port = target_port

    if not target_ip or not target_port:
        log_proxy("未配置 ukey_proxy_target_ip 或 ukey_proxy_target_port，跳过代理启动")
        return

    # 启动 asyncio循环
    # Windows 下 asyncio 的 SelectorEventLoop 某些情况有兼容性问题，ProactorEventLoop 通常更好
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(start_server_async(local_port, target_ip, target_port))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if "Address already in use" in str(e):
            log_proxy(f"端口 {local_port} 已被占用，可能是 Ukey 助手已在本地运行或有僵尸进程，代理不启动。")
        else:
            log_proxy(f"代理启动失败: {e}")


if __name__ == '__main__':
    # 独立运行测试用
    run_proxy_server()