dag/derived_node.py:171:37 [bad-argument-type] Argument `object` is not assignable to parameter `name` with type `str` in function `_HelperRef.__new__`
dag/derived_node.py:235:5 [not-iterable] Type `_CallTargets` is not iterable
dag/derived_node.py:240:9 [not-iterable] Type `_HelperSources` is not iterable
dag/node.py:61:37 [missing-attribute] Object of class `PersistedNodeMixin` has no attribute `_id`
dag/node.py:93:23 [missing-attribute] Object of class `PersistedNodeMixin` has no attribute `_adapter`
dag/node.py:138:17 [missing-attribute] Object of class `PersistedNodeMixin` has no attribute `_id`
dag/node.py:153:26 [missing-attribute] Object of class `PersistedNodeMixin` has no attribute `_id`
houses/council_tax.py:662:18 [unsupported-operation] `None` is not subscriptable
houses/nodes/property_nodes.py:240:40 [bad-argument-type] Argument `None` is not assignable to parameter `commute_breakdown_node` with type `Node[Unknown]` in function `houses.nodes.total_monthly_housing_cost_node.HousingCostConfig.__init__`
houses/nodes/total_monthly_housing_cost_node.py:212:13 [bad-argument-type] Argument `tuple[Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown] | None] | tuple[Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown] | None, Node[Unknown]] | tuple[Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown] | None, Node[Unknown], Node[Unknown]] | tuple[Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown], Node[Unknown] | None, Node[Unknown], Node[Unknown], Node[Unknown]]` is not assignable to parameter `deps` with type `tuple[Node[Unknown], ...]` in function `dag.derived_node.DerivedNode.__init__`
houses/nodes/total_monthly_housing_cost_node.py:284:37 [bad-argument-type] Argument `set[str]` is not assignable to parameter `owners` with type `list[Unknown]` in function `_AdultsSplit.__init__`
houses/services.py:390:83 [unexpected-keyword] Unexpected keyword argument `acceptable` in function `houses.schools.find_nearest`
tests/e2e/test_commute_map_render.py:31:22 [missing-argument] Missing argument `assets` in function `tools.commute.combined_map.build_html`
tests/e2e/test_commute_map_render.py:34:9 [unexpected-keyword] Unexpected keyword argument `leaflet_js` in function `tools.commute.combined_map.build_html`
tests/e2e/test_commute_map_render.py:35:9 [unexpected-keyword] Unexpected keyword argument `leaflet_css` in function `tools.commute.combined_map.build_html`
tests/e2e/test_commute_map_render.py:36:9 [unexpected-keyword] Unexpected keyword argument `icons` in function `tools.commute.combined_map.build_html`
tests/unit/nodes/test_commute.py:431:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/nodes/test_commute.py:436:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/nodes/test_commute.py:507:18 [bad-argument-type] Argument `None` is not assignable to parameter `value` with type `Commute` in function `tests.helpers.FixedCommuteNode.push`
tests/unit/test_car_park.py:348:42 [bad-argument-type] Argument `CarPark | None` is not assignable to parameter `car_park` with type `CarPark` in function `houses.car_park.ApcoaCarParkLookup.load_costs`
tests/unit/test_commute_combined_map.py:100:37 [invalid-argument] Expected a mapping, got int | list[list[float]] | str | dict[str, float | str]
tests/unit/test_commute_combined_map.py:195:35 [invalid-argument] Expected a mapping, got int | list[list[float]] | str | dict[str, float | str]
tests/unit/test_commute_intersection.py:96:19 [bad-argument-type] Argument `float | str | Unknown` is not assignable to parameter `lat` with type `float` in function `houses.geopoint.GeoPoint.__init__`
tests/unit/test_commute_intersection.py:96:24 [bad-argument-type] Argument `float | str | Unknown` is not assignable to parameter `lon` with type `float` in function `houses.geopoint.GeoPoint.__init__`
tests/unit/test_commute_intersection.py:96:45 [not-iterable] Type `int` is not iterable
tests/unit/test_commute_intersection.py:98:12 [bad-index] Cannot index into `str`
tests/unit/test_commute_intersection.py:204:63 [bad-index] Cannot index into `str`
tools/commute/searches.py:207:17 [bad-argument-type] Argument `list[list[float]]` is not assignable to parameter `coords` with type `list[tuple[float, float]]` in function `tools.commute.rightmove_url.build_search_url`
scripts/migrate_destinations.py:43:29 [bad-assignment]
tests/unit/nodes/test_commute.py:409:16 [missing-attribute]
tests/unit/nodes/test_commute.py:414:16 [missing-attribute]
tests/unit/nodes/test_commute.py:481:18 [bad-argument-type]
tests/unit/test_auth.py:37:38 [unsupported-operation]
tests/unit/test_commute_combined_map.py:97:37 [invalid-argument]
tests/unit/test_commute_combined_map.py:192:35 [invalid-argument]
tests/unit/test_commute_intersection.py:93:64 [bad-index]
tests/unit/test_commute_intersection.py:94:25 [bad-argument-type]
tests/unit/test_commute_intersection.py:179:63 [bad-index]
