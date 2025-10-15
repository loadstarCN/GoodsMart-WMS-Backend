import os
from config import DevelopmentConfig, TestingConfig, ProductionConfig

def test_development_config():
    """测试开发环境配置"""
    os.environ["FLASK_ENV"] = "dev"
    config = DevelopmentConfig()
    assert config.DEBUG is True
    assert config.SQLALCHEMY_TRACK_MODIFICATIONS is False

def test_testing_config():
    """测试测试环境配置"""
    os.environ["FLASK_ENV"] = "test"
    config = TestingConfig()
    assert config.TESTING is True    
    assert config.SQLALCHEMY_TRACK_MODIFICATIONS is False

def test_production_config():
    """测试生产环境配置"""
    os.environ["FLASK_ENV"] = "prod"
    config = ProductionConfig()
    assert config.DEBUG is False
    assert config.SQLALCHEMY_TRACK_MODIFICATIONS is False
