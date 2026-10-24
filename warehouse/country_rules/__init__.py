"""国家规则包（doc/shipping-flow.md §6.3）：国家专有的报关检查、单证标签、地址排版、电话写法。

核心只调用 rules_for(国家代码)，不出现国家判断；见 base.py（接口与默认规则）、registry.py（登记）、jp.py（日本）。
"""
from .base import CountryRules
from .registry import DEFAULT_RULES, register, registry, rules_for

__all__ = ['CountryRules', 'DEFAULT_RULES', 'register', 'registry', 'rules_for']
