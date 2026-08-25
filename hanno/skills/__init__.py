"""Miner-registered skills: real local tools (a CAS, a graph checker, a
numeric search script) the Excavator model can call mid-generation to
actually test its ideas, instead of only asserting them. Each skill is one
YAML file in the configured skills directory — the miner describes the tool
and how to invoke it; Hanno exposes it to the model as an Anthropic tool
and records every invocation in the append-only ledger."""
