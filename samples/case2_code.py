from dataclasses import dataclass

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
