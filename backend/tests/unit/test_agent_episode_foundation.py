from app.agent.episode import canonical_hash, public_fact_refs


def test_canonical_hash_is_stable_for_mapping_order():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_public_fact_refs_exclude_values_and_labels():
    refs = public_fact_refs(
        [
            {
                "kind": "inventory",
                "source_tool": "get_inventory_availability",
                "entity_id": 42,
                "field": "available_quantity",
                "value": "secret-or-large-value",
                "label": "human label",
            }
        ]
    )
    assert refs == [
        {
            "kind": "inventory",
            "source_tool": "get_inventory_availability",
            "entity_id": 42,
            "field": "available_quantity",
        }
    ]
    assert "value" not in refs[0]
    assert "label" not in refs[0]
