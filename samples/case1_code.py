import redis
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
