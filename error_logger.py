# ═══════════════════════════════════════════════════════
# error_logger.py — UNCHANGED
# Every error/success from mongo.py, interpolate.py, api.py
# gets saved here, in MongoDB's pipeline_errors collection.
# ═══════════════════════════════════════════════════════
import pymongo
from datetime import datetime

MONGO_URL        = "mongodb://localhost:27017"
DB_NAME          = "weather_db"
ERROR_COLLECTION = "pipeline_errors"


def log_error(source, stage, error_type, error_msg, variable=None, extra=None):
    try:
        client     = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db         = client[DB_NAME]
        collection = db[ERROR_COLLECTION]

        error_doc = {
            "timestamp" : datetime.now().isoformat(),
            "source"    : source,
            "stage"     : stage,
            "error_type": error_type,
            "error_msg" : str(error_msg),
            "variable"  : variable,
            "extra"     : extra or {},
            "status"    : "failed"
        }

        collection.insert_one(error_doc)
        client.close()
        print(f"  [ERROR LOGGED] {stage} | {source} | {error_type}")

    except Exception as log_err:
        print(f"  [ERROR LOGGER FAILED] Could not save to MongoDB: {log_err}")
        print(f"  Original error: {source} | {stage} | {error_msg}")


def log_success(source, stage, records=None, extra=None):
    try:
        client     = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db         = client[DB_NAME]
        collection = db[ERROR_COLLECTION]

        success_doc = {
            "timestamp" : datetime.now().isoformat(),
            "source"    : source,
            "stage"     : stage,
            "error_type": None,
            "error_msg" : None,
            "records"   : records,
            "extra"     : extra or {},
            "status"    : "success"
        }

        collection.insert_one(success_doc)
        client.close()

    except Exception as e:
        print(f"  [SUCCESS LOGGER FAILED] {e}")


def get_all_errors():
    try:
        client     = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db         = client[DB_NAME]
        collection = db[ERROR_COLLECTION]
        errors = list(collection.find({}, {"_id": 0}).sort("timestamp", -1).limit(100))
        client.close()
        return errors
    except Exception as e:
        return [{"error": f"Could not fetch errors: {str(e)}"}]


def get_today_errors():
    try:
        today  = datetime.now().strftime("%Y-%m-%d")
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db     = client[DB_NAME]
        collection = db[ERROR_COLLECTION]
        errors = list(collection.find({"timestamp": {"$regex": f"^{today}"}}, {"_id": 0}).sort("timestamp", -1))
        client.close()
        return errors
    except Exception as e:
        return [{"error": f"Could not fetch today's errors: {str(e)}"}]