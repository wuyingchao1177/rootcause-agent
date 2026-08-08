import threading
from concurrent.futures import ThreadPoolExecutor

class NotificationService:
    """异步通知服务"""

    def __init__(self):
        self.executor = ThreadPoolExecutor(
            max_workers=200,
            thread_name_prefix="notify-",
        )

    def send_batch(self, users: list[int]) -> None:
        """给一批用户发通知（异步）"""
        for uid in users:
            self.executor.submit(self.send_one, uid)  # 提交不等待

    def send_one(self, user_id: int) -> None:
        """发送单条通知"""
        try:
            self.sms_gateway.send(user_id)
        except Exception:
            self.retry_queue.put(user_id)
