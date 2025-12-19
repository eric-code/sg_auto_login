import json

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import re
import time
from utils import log


class MqttCodeListener:
    def __init__(self, config):
        self.config = config
        self.topic = config.get('mqtt_topic')
        self.qos = config.get('mqtt_qos')
        client_id = 'sg_auto_login'
        self.client = mqtt.Client(
            callback_api_version = CallbackAPIVersion.VERSION2,
            client_id = client_id
        )
        self.client.username_pw_set(config.get('mqtt_username'), config.get('mqtt_password'))
        self.received_code = None
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """
        连接成功后的回调
        """
        if reason_code == 0:
            log(f"MQTT 连接成功，正在订阅主题: {self.topic}")
            client.subscribe(self.topic, qos=self.qos)
        else:
            log(f"MQTT 连接失败，原因码: {reason_code}")

    def _on_message(self, client, userdata, msg):
        try:
            raw_payload = msg.payload.decode('utf-8').strip()
            log(f"MQTT 收到原始消息: {raw_payload}")

            #归一化：中文字符转英文字符
            table = str.maketrans({'：': ':', '“': '"', '”': '"', '‘': "'", '’': "'", '，': ','})
            payload_str = raw_payload.translate(table)

            msg_user = None
            code = None

            #尝试标准解析 (处理可能存在的双重 JSON)
            try:
                data = json.loads(payload_str)
                if isinstance(data, str):
                    data = json.loads(data)
                if isinstance(data, dict):
                    msg_user = data.get('username')
                    code = data.get('code')
            except Exception:
                pass

            #强力正则提取 (支持字母+数字验证码)
            if not msg_user or not code:
                if not msg_user:
                    m = re.search(r'username[\\"\']*:\s*[\\"\']*([^\\"\s,}\]]+)', payload_str)
                    msg_user = m.group(1) if m else None
                if not code:
                    m = re.search(r'code[\\"\']*:\s*[\\"\']*([^\\"\s,}\]]+)', payload_str)
                    code = m.group(1) if m else None

            #最终清理：剥掉首尾残留的各种包装
            def clean(v):
                if v is None: return None
                # 去掉首尾空格、各种引号、斜杠、逗号、括号
                return str(v).strip(' \t\n\r"\'\\{}[] ,')

            msg_user = clean(msg_user)
            code = clean(code)

            if not code:
                log("未能提取到有效的验证码")
                return

            target_user = clean(self.config.get('username', ''))
            log(f"解析结果 -> 用户: [{msg_user}], 验证码: [{code}]")

            if target_user and msg_user and msg_user != target_user:
                log(f"用户不匹配，忽略。")
                return

            self.received_code = code
            log(f"成功捕获验证码: {self.received_code}")

        except Exception as e:
            log(f"处理 MQTT 消息时发生未知错误: {e}")

    def start(self):
        try:
            log(f"正在连接 MQTT 服务器: {self.config['mqtt_host']}...")
            raw_port = self.config.get('mqtt_port')
            mqtt_port = int(raw_port)
            self.client.connect(
                self.config['mqtt_host'],
                mqtt_port,
                60
            )
            # loop_start 会启动一个后台线程，不断调用上面那些回调函数
            self.client.loop_start()
        except Exception as e:
            log(f"MQTT 启动失败: {e}")

    def get_code(self, timeout=60):
        log(f"开始在主题 {self.topic} 中等待验证码...")
        self.received_code = None
        start_t = time.time()
        while time.time() - start_t < timeout:
            if self.received_code:
                return self.received_code
            time.sleep(1)
        return None

    def stop(self):
        log("停止 MQTT 监听器...")
        self.client.loop_stop()
        self.client.disconnect()
