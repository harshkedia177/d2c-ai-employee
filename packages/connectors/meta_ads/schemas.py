SCHEMAS: dict[str, dict] = {
    "campaigns": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "status": {"type": "string"},
            "objective": {"type": "string"},
        },
        "required": ["id"],
    },
    "ad_insights": {
        "type": "object",
        "properties": {
            "date_start": {"type": "string", "format": "date"},
            "campaign_id": {"type": "string"},
            "campaign_name": {"type": "string"},
            "ad_id": {"type": "string"},
            "ad_set_id": {"type": "string"},
            "spend": {"type": "string"},
            "impressions": {"type": "integer"},
            "clicks": {"type": "integer"},
            "conversions": {"type": "integer"},
            "purchase_roas": {"type": "array"},
        },
        "required": ["date_start", "campaign_id", "ad_id", "spend"],
    },
}
