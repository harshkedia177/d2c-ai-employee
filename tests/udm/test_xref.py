from packages.udm.xref import canonical_id


def test_canonical_id_is_deterministic():
    a = canonical_id("t1", "order", "shopify", "12345")
    b = canonical_id("t1", "order", "shopify", "12345")
    assert a == b


def test_canonical_id_differs_per_tenant():
    assert canonical_id("t1", "order", "shopify", "12345") != canonical_id(
        "t2", "order", "shopify", "12345"
    )


def test_canonical_id_differs_per_entity():
    assert canonical_id("t1", "order", "shopify", "12345") != canonical_id(
        "t1", "shipment", "shopify", "12345"
    )


def test_canonical_id_differs_per_source_system():
    assert canonical_id("t1", "order", "shopify", "12345") != canonical_id(
        "t1", "order", "woocommerce", "12345"
    )


def test_canonical_id_is_a_uuid():
    import uuid

    val = canonical_id("t1", "order", "shopify", "12345")
    assert uuid.UUID(val)
