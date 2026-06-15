from houses.model.registry import NodeKind, node

node(
    id="rightmove_url",
    kind=NodeKind.source,
    provenance_template="Browser extension",
)(lambda: None)

node(
    id="rightmove_address",
    kind=NodeKind.source,
    provenance_template="Rightmove",
)(lambda: None)

node(
    id="rightmove_bedrooms",
    kind=NodeKind.source,
    provenance_template="Rightmove",
)(lambda: None)

node(
    id="rightmove_price",
    kind=NodeKind.source,
    provenance_template="Rightmove",
)(lambda: None)

node(
    id="rightmove_location",
    kind=NodeKind.source,
    provenance_template="Rightmove map",
)(lambda: None)
