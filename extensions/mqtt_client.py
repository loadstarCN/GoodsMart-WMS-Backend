# extensions/mqtt_client.py
import json
import paho.mqtt.client as mqtt
from flask import current_app

class MQTTClient:
    def __init__(self, app=None):
        self.client = None
        self.message_handlers = {}
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化 MQTT 客户端"""
        # 从应用配置中获取 MQTT 设置
        broker = app.config.get("MQTT_BROKER_URL")
        port = app.config.get("MQTT_BROKER_PORT", 1883)
        
        if not broker:
            print("MQTT_BROKER_URL configuration is missing")
            return
        
        # 创建客户端并设置回调
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        
        # 如果配置了用户名密码，进行认证
        if username := app.config.get("MQTT_USERNAME"):
            password = app.config.get("MQTT_PASSWORD", "")
            self.client.username_pw_set(username, password)
        
        # 连接 broker
        try:
            self.client.connect(broker, port, 60)
            self.client.loop_start()
            print(f"MQTT connecting to {broker}:{port}")
        except Exception as e:
            print(f"MQTT connection failed: {str(e)}")
        
        # 注册到应用扩展
        if not hasattr(app, 'extensions'):
            app.extensions = {}
        app.extensions['mqtt'] = self
    
    def _on_connect(self, client, userdata, flags, rc):
        """连接建立时的回调"""
        if rc != 0:
            print(f"MQTT connection failed with code {rc}")
            return
        
        print("MQTT connected successfully")
        
        # 重新订阅之前注册的主题
        for topic in self.message_handlers.keys():
            self.client.subscribe(topic)
            current_app.logger.debug(f"Resubscribed to topic: {topic}")

    def _on_message(self, client, userdata, msg):
        """接收到消息时的回调"""
        try:
            # 获取消息主题
            topic = msg.topic
            
            # 如果有该主题的消息处理器
            if topic in self.message_handlers:
                # 尝试解析 JSON
                try:
                    payload = json.loads(msg.payload.decode())
                except UnicodeDecodeError:
                    payload = msg.payload.decode()  # 非JSON消息
                except json.JSONDecodeError:
                    print(f"Invalid JSON in MQTT payload for topic {topic}")
                    return
                
                # 获取所有该主题的处理函数
                handlers = self.message_handlers[topic]
                for handler in handlers:
                    # 在应用上下文中调用处理函数
                    with current_app.app_context():
                        try:
                            handler(topic, payload)
                        except Exception as e:
                            print(f"Error in MQTT handler for topic {topic}: {str(e)}")
            else:
                current_app.logger.debug(f"No handler for MQTT topic: {topic}")
                
        except Exception as e:
            print(f"Error processing MQTT message: {str(e)}", exc_info=True)

    def subscribe(self, topic, handler):
        """订阅主题并注册消息处理函数"""
        if not self.client:
            print("MQTT client not initialized")
            return False
        
        # 添加主题到处理函数映射
        if topic not in self.message_handlers:
            self.message_handlers[topic] = []
        
        # 避免重复添加相同的处理器
        if handler not in self.message_handlers[topic]:
            self.message_handlers[topic].append(handler)
            print(f"Added handler for MQTT topic: {topic}")
        
        # 订阅主题
        result, mid = self.client.subscribe(topic)
        if result == mqtt.MQTT_ERR_SUCCESS:
            current_app.logger.debug(f"Subscribed to topic: {topic}")
            return True
        else:
            print(f"Failed to subscribe to topic {topic}: error {result}")
            return False

    def publish(self, topic, payload, qos=0, retain=False):
        """发布 MQTT 消息"""
        if not self.client:
            print("MQTT client not initialized")
            return False
        
        # 如果 payload 是字典，转换为 JSON
        if isinstance(payload, dict):
            try:
                payload = json.dumps(payload)
            except TypeError:
                print(f"Failed to serialize payload for topic {topic}")
                return False
        
        # 发布消息
        try:
            result = self.client.publish(topic, payload, qos=qos, retain=retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"Failed to publish MQTT message: {mqtt.error_string(result.rc)}")
                return False
            current_app.logger.debug(f"Sent MQTT message to {topic}")
            return True
        except Exception as e:
            print(f"Error sending MQTT message: {str(e)}")
            return False

    def unsubscribe(self, topic, handler=None):
        """取消订阅或移除特定处理函数"""
        if topic in self.message_handlers:
            # 移除特定处理函数
            if handler:
                if handler in self.message_handlers[topic]:
                    self.message_handlers[topic].remove(handler)
                    print(f"Removed handler for topic {topic}")
            
            # 如果所有处理函数都已移除或未指定处理函数
            if not handler or not self.message_handlers[topic]:
                # 完全取消订阅
                result, mid = self.client.unsubscribe(topic)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    del self.message_handlers[topic]
                    print(f"Unsubscribed from topic {topic}")
                    return True
                else:
                    print(f"Failed to unsubscribe from topic {topic}: error {result}")
                    return False
        
        return True

# 创建全局扩展实例
mqtt_client = MQTTClient()