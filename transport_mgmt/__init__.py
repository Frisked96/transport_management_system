# Transport Management System
import sys
if sys.version_info >= (3, 14):
    try:
        from django.template.context import BaseContext
        def _base_context_copy(self):
            duplicate = object.__new__(self.__class__)
            duplicate.dicts = self.dicts[:]
            return duplicate
        BaseContext.__copy__ = _base_context_copy
    except ImportError:
        pass
