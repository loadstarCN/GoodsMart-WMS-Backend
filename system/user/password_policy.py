"""新密码的复杂度规则（所有「设新密码」的入口共用；与 Web 修改密码页的 PASSWORD_REGEX 一致）

8–16 位；至少一个小写字母、一个大写字母、一个数字、一个特殊符号（@$!%*?&）；只能由英文字母、数字和这些特殊符号组成。
不合规 → 400 10015（field = 请求里的字段名；details {min_length, max_length, special_characters}，界面按它翻译）。

入口：忘记密码 → 验证码 → 设新密码（UserService.reset_password）、修改密码（change_password）、新建 / 修改平台用户
（create_user / update_user 带 password）、新建 / 修改员工（StaffService.create_staff / update_staff 带 password）。
只检查新设的密码：已有密码照常能登录，不受影响。
"""
import re

from extensions.error import BadRequestException

PASSWORD_POLICY_CODE = 10015
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 16
PASSWORD_SPECIAL_CHARACTERS = '@$!%*?&'

# 同前端 /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,16}$/：用 [0-9] 而不是 \d
# （Python 的 \d 还匹配全角等 Unicode 数字），fullmatch 不放过结尾的换行
_PASSWORD_RE = re.compile(
    r'(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[@$!%*?&])[A-Za-z0-9@$!%*?&]'
    + f'{{{PASSWORD_MIN_LENGTH},{PASSWORD_MAX_LENGTH}}}'
)


def is_valid_password(password) -> bool:
    return isinstance(password, str) and _PASSWORD_RE.fullmatch(password) is not None


def validate_new_password(password, field: str = 'password') -> str:
    """新密码不合规 → 400 10015；合规原样返回"""
    if not is_valid_password(password):
        raise BadRequestException(
            f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters long and contain an uppercase "
            f"letter, a lowercase letter, a digit and a special character ({PASSWORD_SPECIAL_CHARACTERS}); "
            "only letters, digits and these special characters are allowed",
            PASSWORD_POLICY_CODE, field=field,
            details={'min_length': PASSWORD_MIN_LENGTH, 'max_length': PASSWORD_MAX_LENGTH,
                     'special_characters': PASSWORD_SPECIAL_CHARACTERS},
        )
    return password
