import os
import uuid
from flask import current_app
import oss2
from extensions.error import BadRequestException
from werkzeug.utils import secure_filename

def allowed_image_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ['png', 'jpg', 'jpeg', 'gif' ]

class OSS(object):
    def __init__(self, app=None):
        self.bucket = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        auth = oss2.Auth(app.config.get("OSS_ACCESS_KEY_ID"), app.config.get("OSS_ACCESS_KEY_SECRET"))
        self.bucket = oss2.Bucket(auth, app.config.get("OSS_ENDPOINT"), app.config.get("OSS_BUCKET_NAME"))
        if not hasattr(app, 'extensions'):
            app.extensions = {}
        app.extensions['oss'] = self

    def upload_file(self, file, path,random_name=True):
        # 在接收文件前验证
        if not allowed_image_file(file.filename):
            raise BadRequestException("Invalid file type. Only PNG, JPG, JPEG, and GIF are allowed.", 10001)
        # 生成安全随机文件名
        original_filename = secure_filename(file.filename)        
        if random_name:
            file_ext = os.path.splitext(original_filename)[1].lower()  # 统一小写
            # 生成随机文件名
            random_name = f"{uuid.uuid4().hex}{file_ext}"
        else:
            # 使用原文件名
            random_name = original_filename
        
        # OSS上传
        oss_path = path + random_name
        try:
            oss.bucket.put_object(oss_path, file)
        except oss2.exceptions.OssError as e:
            raise BadRequestException(f"OSS upload failed: {e}", 10002)
        
        return current_app.config.get("OSS_HOST")+"/"+oss_path

oss = OSS()
