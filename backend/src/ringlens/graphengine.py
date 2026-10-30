"""Where the Graph Data Science algorithms run.

* ``plugin`` (default, local): the GDS plugin inside Neo4j, called through Cypher
  (``gds.graph.project`` / ``CALL gds.<algo>.write``). This is the setup ``start.ps1`` installs.
* ``aura``: Neo4j AuraDB Free has no GDS plugin, so the same algorithms run in an
  Aura Graph Analytics session (the ``graphdatascience`` client). The projections pull the
  graph from AuraDB with ``gds.graph.project.remote`` and every algorithm writes its result
  back to AuraDB, so the rest of the pipeline reads identical properties either way.

Both engines project the same five graphs and write the same properties:
``scc``, ``community``, ``pagerank``, ``betweenness`` and ``identity_cc`` on Provider nodes and
``RL_SIMILAR {jaccard}`` relationships between providers with overlapping patients.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from .db import cypher
from .settings import get_settings

log = logging.getLogger("ringlens.graph")

PROJECTIONS = ["rl_strong", "rl_referrals", "rl_referrals_u", "rl_bipartite", "rl_identity"]


# ---------------------------------------------------------------- local GDS plugin
class PluginEngine:
    name = "plugin"

    def version(self) -> str:
        return cypher("RETURN gds.version() AS v")[0]["v"]

    def drop(self):
        for g in PROJECTIONS:
            cypher("CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", name=g)

    def project(self, mc: int, mp: int) -> dict:
        self.drop()
        out = {}
        out["strong"] = cypher("""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            WHERE r.count >= $mc AND r.patients >= $mp
            WITH gds.graph.project('rl_strong', a, b, {relationshipProperties: {weight: toFloat(r.count)}}) AS g
            RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""", mc=mc, mp=mp)[0]
        out["referrals"] = cypher("""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            WITH gds.graph.project('rl_referrals', a, b, {relationshipProperties: {weight: toFloat(r.count)}}) AS g
            RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
        cypher("""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            WITH gds.graph.project('rl_referrals_u', a, b, {relationshipProperties: {weight: toFloat(r.count)}},
                                   {undirectedRelationshipTypes: ['*']}) AS g
            RETURN g.nodeCount AS nodes""")
        out["bipartite"] = cypher("""
            MATCH (p:Provider)-[t:TREATED]->(pt:Patient)
            WITH p, pt, count(t) AS w
            WITH gds.graph.project('rl_bipartite', p, pt, {sourceNodeLabels: ['Provider'], targetNodeLabels: ['Patient'],
                                   relationshipProperties: {weight: toFloat(w)}}) AS g
            RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
        out["identity"] = cypher("""
            MATCH (p:Provider)-[:PAID_TO]->(b:BankAccount)
            WITH gds.graph.project('rl_identity', p, b, {}, {undirectedRelationshipTypes: ['*']}) AS g
            RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
        return out

    def scc(self):
        cypher("CALL gds.scc.write('rl_strong', {writeProperty: 'scc'}) YIELD componentCount RETURN componentCount")

    def louvain(self):
        cypher("CALL gds.louvain.write('rl_referrals_u', {writeProperty: 'community', "
               "relationshipWeightProperty: 'weight'}) YIELD communityCount RETURN communityCount")

    def pagerank(self):
        cypher("CALL gds.pageRank.write('rl_referrals', {writeProperty: 'pagerank', "
               "relationshipWeightProperty: 'weight'}) YIELD ranIterations RETURN ranIterations")

    def betweenness(self):
        cypher("CALL gds.betweenness.write('rl_referrals', {writeProperty: 'betweenness'}) "
               "YIELD centralityDistribution RETURN centralityDistribution.max AS max")

    def similarity(self, cutoff: float):
        cypher("""
            CALL gds.nodeSimilarity.write('rl_bipartite', {topK: 3, similarityCutoff: $cutoff, degreeCutoff: 15,
                 similarityMetric: 'JACCARD', writeRelationshipType: 'RL_SIMILAR', writeProperty: 'jaccard'})
            YIELD relationshipsWritten RETURN relationshipsWritten""", cutoff=cutoff)

    def wcc_identity(self):
        cypher("CALL gds.wcc.write('rl_identity', {writeProperty: 'identity_cc'}) YIELD componentCount "
               "RETURN componentCount")

    def close(self):
        pass


# ---------------------------------------------------------------- Aura Graph Analytics
REMOTE = "gds.graph.project.remote"


class AuraEngine:
    """GDS algorithms in an Aura Graph Analytics session attached to the AuraDB instance."""

    name = "aura"

    def __init__(self):
        self.s = get_settings()
        self._gds = None
        self.graphs: dict = {}

    def version(self) -> str:
        import graphdatascience

        return f"Aura Graph Analytics (graphdatascience {graphdatascience.__version__})"

    @property
    def gds(self):
        if self._gds is None:
            from graphdatascience.session import AuraAPICredentials, DbmsConnectionInfo, GdsSessions

            s = self.s
            if not (s.aura_client_id and s.aura_client_secret):
                raise RuntimeError("RINGLENS_GDS_MODE=aura needs RINGLENS_AURA_CLIENT_ID and "
                                   "RINGLENS_AURA_CLIENT_SECRET (Aura console > Account > API keys)")
            sessions = GdsSessions(api_credentials=AuraAPICredentials(
                s.aura_client_id, s.aura_client_secret, s.aura_project_id or None))
            t0 = time.perf_counter()
            log.info("starting Aura Graph Analytics session (%s)", s.aura_session_memory)
            self._gds = sessions.get_or_create(
                session_name=s.aura_session_name, memory=s.aura_session_memory,
                db_connection=DbmsConnectionInfo(uri=s.neo4j_uri, username=s.neo4j_user,
                                                 password=s.neo4j_password),
                ttl=timedelta(minutes=30), timeout=600, show_progress=False)
            log.info("Aura Graph Analytics session ready in %.1fs", time.perf_counter() - t0)
        return self._gds

    def _project(self, name: str, query: str, params: dict | None = None,
                 undirected: list[str] | None = None) -> dict:
        res = self.gds.graph.project.cypher(name, query, query_parameters=params or {},
                                            undirected_relationship_types=undirected, overwrite=True)
        G, info = (res.graph, res.result) if hasattr(res, "graph") else res
        self.graphs[name] = G
        node_count = getattr(info, "node_count", None) or getattr(G, "node_count", lambda: None)()
        rel_count = getattr(info, "relationship_count", None) or getattr(G, "relationship_count", lambda: None)()
        return {"nodes": node_count, "rels": rel_count}

    def drop(self):
        if self._gds is None:
            return
        for g in PROJECTIONS:
            try:
                self.gds.graph.drop(g, fail_if_missing=False)
            except Exception:  # noqa: BLE001 - nothing to drop
                pass
        self.graphs.clear()

    def project(self, mc: int, mp: int) -> dict:
        out = {}
        out["strong"] = self._project("rl_strong", f"""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            WHERE r.count >= $mc AND r.patients >= $mp
            RETURN {REMOTE}(a, b, {{sourceNodeLabels: ['Provider'], targetNodeLabels: ['Provider'],
                   relationshipType: 'REFERS_TO', relationshipProperties: {{weight: toFloat(r.count)}}}})""",
                                      {"mc": mc, "mp": mp})
        out["referrals"] = self._project("rl_referrals", f"""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            RETURN {REMOTE}(a, b, {{sourceNodeLabels: ['Provider'], targetNodeLabels: ['Provider'],
                   relationshipType: 'REFERS_TO', relationshipProperties: {{weight: toFloat(r.count)}}}})""")
        self._project("rl_referrals_u", f"""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
            RETURN {REMOTE}(a, b, {{sourceNodeLabels: ['Provider'], targetNodeLabels: ['Provider'],
                   relationshipType: 'REFERS_TO', relationshipProperties: {{weight: toFloat(r.count)}}}})""",
                      undirected=["REFERS_TO"])
        out["bipartite"] = self._project("rl_bipartite", f"""
            MATCH (p:Provider)-[t:TREATED]->(pt:Patient)
            WITH p, pt, count(t) AS w
            RETURN {REMOTE}(p, pt, {{sourceNodeLabels: ['Provider'], targetNodeLabels: ['Patient'],
                   relationshipType: 'TREATED', relationshipProperties: {{weight: toFloat(w)}}}})""")
        out["identity"] = self._project("rl_identity", f"""
            MATCH (p:Provider)-[:PAID_TO]->(b:BankAccount)
            RETURN {REMOTE}(p, b, {{sourceNodeLabels: ['Provider'], targetNodeLabels: ['BankAccount'],
                   relationshipType: 'PAID_TO'}})""", undirected=["PAID_TO"])
        return out

    def scc(self):
        self.gds.scc.write(self.graphs["rl_strong"], write_property="scc")

    def louvain(self):
        self.gds.louvain.write(self.graphs["rl_referrals_u"], write_property="community",
                               relationship_weight_property="weight")

    def pagerank(self):
        self.gds.page_rank.write(self.graphs["rl_referrals"], write_property="pagerank",
                                 relationship_weight_property="weight")

    def betweenness(self):
        self.gds.betweenness.write(self.graphs["rl_referrals"], write_property="betweenness")

    def similarity(self, cutoff: float):
        self.gds.node_similarity.write(self.graphs["rl_bipartite"], write_relationship_type="RL_SIMILAR",
                                       write_property="jaccard", top_k=3, similarity_cutoff=cutoff,
                                       degree_cutoff=15, similarity_metric="JACCARD")

    def wcc_identity(self):
        self.gds.wcc.write(self.graphs["rl_identity"], write_property="identity_cc")

    def close(self):
        """Delete the session: AuraDB Free allows one at a time, and results are already written."""
        if self._gds is not None:
            try:
                self._gds.delete()
            except Exception as exc:  # noqa: BLE001
                log.warning("could not delete the Aura Graph Analytics session: %s", exc)
            self._gds = None
            self.graphs.clear()


def engine():
    return AuraEngine() if get_settings().gds_mode == "aura" else PluginEngine()


def version() -> str:
    try:
        return engine().version()
    except Exception as exc:  # noqa: BLE001 - shown on the Internals page, never fatal
        return f"unavailable ({type(exc).__name__})"
