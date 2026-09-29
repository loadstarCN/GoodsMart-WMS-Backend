from datetime import datetime

def filter_none(data):
    """
    Recursively remove None values from a dictionary or list.
    """
    if isinstance(data, dict):
        return {k: filter_none(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [filter_none(item) for item in data if item is not None]
    return data

def generate_input_fields(model):
    """从输出模型生成输入模型：剔除只读字段。
    flask-restx 把 readOnly=True 存成 field.readonly（小写），两种写法都要识别。
    """
    return {
        key: field for key, field in model.items()
        if not (getattr(field, 'readonly', False) or getattr(field, 'readOnly', False))
    }

def parse_date(date_str):
    if not date_str or not date_str.strip():
        return None
    try:
        # 日付形式を検証 (例: YYYY-MM-DD)
        return datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
    except ValueError:
        return None  # 不正な形式は NULL 扱い