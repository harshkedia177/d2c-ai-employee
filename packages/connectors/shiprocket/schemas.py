SCHEMAS: dict[str, dict] = {
    "shipments": {
        "type": "object",
        "properties": {
            "shipment_id": {"type": ["integer", "string"]},
            "order_id": {"type": "string"},
            "awb_code": {"type": "string"},
            "courier_name": {"type": "string"},
            "current_status": {"type": "string"},
            "is_rto": {"type": "boolean"},
            "freight_charges": {"type": "number"},
            "shipped_date": {"type": "string", "format": "date-time"},
            "delivered_date": {"type": "string", "format": "date-time"},
        },
        "required": ["shipment_id", "order_id", "current_status", "is_rto"],
    }
}
