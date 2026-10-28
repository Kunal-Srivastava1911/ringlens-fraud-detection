"""Connection helpers for both stores.

Write concern for the system of record is majority + journaled: auditable financial
data must survive a primary failover. Heavy analytical reads use secondaryPreferred so
that, on a replica set with secondaries, they stay off the primary serving claim intake.
"""
from functools import lru_cache

from neo4j import Driver, GraphDatabase
from pymongo import MongoClient, ReadPreference
from pymongo.database import Database
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .settings import get_settings

MAJORITY = WriteConcern(w="majority", j=True)


@lru_cache
def mongo_client() -> MongoClient:
    return MongoClient(get_settings().mongo_uri, uuidRepresentation="standard", tz_aware=True,
                       serverSelectionTimeoutMS=8000)


def mongo_db() -> Database:
    return mongo_client().get_database(get_settings().mongo_db, write_concern=MAJORITY)


def analytics_db() -> Database:
    return mongo_client().get_database(get_settings().mongo_db,
                                       read_preference=ReadPreference.SECONDARY_PREFERRED,
                                       read_concern=ReadConcern("majority"))


def truth_db() -> Database:
    """Ground-truth labels. Only the generator and the evaluation ever touch this database."""
    return mongo_client().get_database(get_settings().truth_db, write_concern=MAJORITY)


@lru_cache
def neo4j_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password),
                                notifications_min_severity="OFF")


def cypher(query: str, **params) -> list[dict]:
    """Run one Cypher statement in a managed transaction and return the records as dicts."""
    records, _, _ = neo4j_driver().execute_query(query, params, database_=get_settings().neo4j_db)
    return [r.data() for r in records]


def cypher_autocommit(query: str, **params) -> None:
    """For statements that manage their own transactions (CALL { } IN TRANSACTIONS)."""
    with neo4j_driver().session(database=get_settings().neo4j_db) as session:
        session.run(query, params).consume()
