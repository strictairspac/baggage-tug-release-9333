from redis.exceptions import RedisError
from src.config.resources import cache, engine
from src.services import archives, cases, runs, reviews


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    wake()
    return run


def wake():
    try:
        cache.lpush("workbench:wake", "1")
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def new_archive(case_id, user):
    with engine.begin() as db:
        job, created = archives.initiate(db, case_id, user["username"])
    wake()
    return job, created
