from django.core.cache import cache


class CachingManager:
    @staticmethod
    def access_key(key):
        return cache.get(key)
    @staticmethod
    def invalidate_cache(key):
        cache.delete(key)