from flask_jwt_extended import JWTManager

jwt = JWTManager()         # JWT 扩展
authorizations = {
	"jsonWebToken": {
		"type": "apiKey",
		"in": "header",
		"name": "Authorization"
	}
}


