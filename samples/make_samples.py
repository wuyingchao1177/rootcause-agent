"""生成 benchmark 示例 case（模拟真实故障场景）。

生成 3 个 case:
  case_1: Redis 连接池耗尽导致超时
  case_2: NPE 空指针
  case_3: 线程池拒绝任务

每个 case 包含: 问题描述、日志（大量重复+关键异常行）、代码文件、ground truth
"""

import json
import random
from pathlib import Path

random.seed(42)

OUT = Path(__file__).parent


# ─── Case 1: Redis 连接池耗尽 ───────────────────────────────────

def make_case1(tmp: Path):
    logs = []
    # 大量正常日志（制造冗余）
    for i in range(3000):
        logs.append(f"2026-07-28 10:{random.randint(0,59):02d}:{random.randint(0,59):02d} INFO  RequestId={random.randint(10000,99999)} GET /api/order/{random.randint(1,9999)} 200 {random.randint(5,80)}ms")
    # 正常 Redis 操作
    for i in range(1000):
        logs.append(f"2026-07-28 10:{random.randint(0,59):02d}:{random.randint(0,59):02d} INFO  Redis SET key:user:{random.randint(1,9999)} OK 1ms")
    # 异常出现
    for i in range(300):
        logs.append(f"2026-07-28 10:45:{random.randint(0,59):02d} ERROR RedisTimeoutException: JedisConnectionException: Could not get a resource from the pool")
    logs.append("2026-07-28 10:45:23 ERROR Caused by: redis.clients.jedis.exceptions.JedisConnectionException: Could not get a resource from the pool")
    logs.append("2026-07-28 10:45:23 ERROR Caused by: java.util.NoSuchElementException: Pool exhausted")
    for i in range(500):
        logs.append(f"2026-07-28 10:4{random.randint(5,9)}:0{random.randint(0,9)} WARN  OrderService fallback: redis unavailable, use local cache")

    code = '''import redis
from redis import Redis

class OrderService:
    """订单服务，依赖 Redis 缓存商品信息"""

    def __init__(self, redis_config):
        self.cache = Redis(
            host=redis_config["host"],
            port=redis_config["port"],
            max_connections=redis_config.get("max_connections", 10),
            timeout=redis_config.get("timeout", 3),
        )
        self.order_dao = OrderDAO()

    def get_order(self, order_id: str) -> dict:
        """获取订单，优先缓存，缓存 miss 查库"""
        cached = self.cache.get(f"order:{order_id}")
        if cached:
            return json.loads(cached)
        order = self.order_dao.query_by_id(order_id)
        self.cache.setex(f"order:{order_id}", 300, json.dumps(order))
        return order

    def batch_query(self, order_ids: list[str]) -> list[dict]:
        """批量查询订单，逐个查缓存"""
        results = []
        for oid in order_ids:
            results.append(self.get_order(oid))  # 每次都要拿连接
        return results
'''
    (tmp / "case1_code.py").write_text(code)

    return {
        "id": "case_1_redis_pool",
        "problem": '订单接口大量超时，报错 "Could not get a resource from the pool"，服务 CPU 正常但 QPS 下降 60%',
        "keywords": ["JedisConnectionException", "Pool exhausted", "max_connections"],
        "logs": logs,
        "code_files": [str(tmp / "case1_code.py")],
        "ground_truth": {
            "root_cause": "Redis 连接池配置 max_connections=10 过小，批量查询导致连接耗尽",
            "keywords": ["连接池", "max_connections", "pool", "exhausted"],
            "evidence": ["Could not get a resource from the pool", "Pool exhausted"],
        },
    }


# ─── Case 2: NPE ────────────────────────────────────────────────

def make_case2(tmp: Path):
    logs = []
    for i in range(2000):
        logs.append(f"2026-07-28 11:{random.randint(0,59):02d}:{random.randint(0,59):02d} INFO  Processing msg_{random.randint(1,99999)}")
    logs.append("2026-07-28 11:23:45 ERROR NullPointerException: null")
    logs.append("2026-07-28 11:23:45 ERROR     at com.demo.user.UserServiceImpl.getUser(UserServiceImpl.java:42)")
    logs.append("2026-07-28 11:23:45 ERROR     at com.demo.user.UserController.hello(UserController.java:28)")
    for i in range(800):
        logs.append(f"2026-07-28 11:2{random.randint(0,9)}:{random.randint(0,59):02d} INFO  Heartbeat ok")

    code = '''from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str
    profile: dict

class UserServiceImpl:
    """用户服务"""

    def __init__(self, user_dao, profile_dao):
        self.user_dao = user_dao
        self.profile_dao = profile_dao

    def getUser(self, user_id: int) -> dict:
        user = self.user_dao.find_by_id(user_id)
        # line 42: 这里如果 user 为 None 会 NPE
        name = user.name
        profile = self.profile_dao.find_by_user_id(user_id)
        return {"id": user.id, "name": name, "profile": profile}
'''
    (tmp / "case2_code.py").write_text(code)

    return {
        "id": "case_2_npe",
        "problem": '调用用户接口偶发 500，日志报 NullPointerException at UserServiceImpl.getUser:42',
        "keywords": ["NullPointerException", "getUser", "UserServiceImpl"],
        "logs": logs,
        "code_files": [str(tmp / "case2_code.py")],
        "ground_truth": {
            "root_cause": "getUser 中 user 为 None 时未判空，直接访问 user.name 导致 NPE",
            "keywords": ["判空", "null", "user.name", "getUser"],
            "evidence": ["NullPointerException", "getUser(UserServiceImpl.java:42)"],
        },
    }


# ─── Case 3: 线程池拒绝 ─────────────────────────────────────────

def make_case3(tmp: Path):
    logs = []
    for i in range(2500):
        logs.append(f"2026-07-28 14:{random.randint(0,59):02d}:{random.randint(0,59):02d} INFO  Task submit task_{random.randint(1,99999)} to pool")
    logs.append("2026-07-28 14:30:11 ERROR RejectedExecutionException: Task java.util.concurrent.FutureTask rejected from java.util.concurrent.ThreadPoolExecutor")
    logs.append("2026-07-28 14:30:11 ERROR Caused by: java.util.concurrent.RejectedExecutionException: pool size=200, active=200, queue capacity=100, queued=100, completed=50000")
    for i in range(600):
        logs.append(f"2026-07-28 14:3{random.randint(0,9)}:0{random.randint(0,9)} WARN  Async task dropped: task_{random.randint(1,99999)}")

    code = '''import threading
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
'''
    (tmp / "case3_code.py").write_text(code)

    return {
        "id": "case_3_threadpool_reject",
        "problem": '大促期间通知任务大量失败，报 RejectedExecutionException，短信发送量骤降',
        "keywords": ["RejectedExecutionException", "ThreadPoolExecutor", "queue"],
        "logs": logs,
        "code_files": [str(tmp / "case3_code.py")],
        "ground_truth": {
            "root_cause": "线程池 max_workers=200 且队列容量默认无界场景下，大促提交量超阈值触发拒绝策略（默认 AbortPolicy）",
            "keywords": ["拒绝策略", "RejectedExecutionException", "队列", "AbortPolicy"],
            "evidence": ["RejectedExecutionException", "queued=100"],
        },
    }


def main():
    for maker, name in [(make_case1, "case_1_redis_pool"), (make_case2, "case_2_npe"), (make_case3, "case_3_threadpool_reject")]:
        case = maker(OUT)
        (OUT / f"{name}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2))
        print(f"生成 {name}: {len(case['logs'])} 行日志")


if __name__ == "__main__":
    main()
