from pymongo import MongoClient

client = MongoClient(
    host="4.194.78.84",
    port=27017,
    username="infinitytestadmin",
    password="Inf!dh7DV45aqz",
    authSource="Infinitytest"
)

db = client["Infinitytest"]

customers = db["customers"]
tickets = db["tickets"]
agents = db["agents"]
admins = db["admins"]
