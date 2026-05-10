SCHEMAS: dict[str, dict] = {
    "orders": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "total_price": {"type": "string"},
            "currency": {"type": "string"},
            "gateway": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
        },
        "required": ["id", "total_price", "updated_at"],
    },
    "line_items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "sku": {"type": "string"},
            "title": {"type": "string"},
            "quantity": {"type": "integer"},
            "price": {"type": "string"},
        },
    },
    "products": {"type": "object"},
    "customers": {"type": "object"},
    "refunds": {"type": "object"},
    "fulfillments": {"type": "object"},
}
