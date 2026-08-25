dag/evaluate.py:42:18 [missing-attribute] Object of class `Node` has no attribute `_call_compute`
dag/expression.py:230:38 [unsupported-operation] `-` is not supported between `None` and `None`
dag/expression.py:250:38 [unsupported-operation] Unary `-` is not supported on `None`
dag/expression.py:275:38 [unsupported-operation] `*` is not supported between `None` and `None`
dag/expression.py:306:38 [unsupported-operation] `/` is not supported between `None` and `None`
dag/persistence.py:44:26 [invalid-argument] Expected class object, got `type[QuantityT]`
dag/scheduler.py:213:5 [missing-attribute] Object of class `RefreshScheduler` has no attribute `_after_refresh_callback`
dag/signals.py:75:13 [not-callable] Expected a callable, got `None`
dag/user_input_node.py:101:38 [invalid-argument] Expected class object, got `type[QuantityT]`
houses/bus_journey.py:248:32 [missing-attribute] Object of class `NoneType` has no attribute `items`
houses/bus_journey.py:275:32 [missing-attribute] Object of class `NoneType` has no attribute `items`
houses/bus_journey.py:327:23 [not-iterable] Type `None` is not iterable
houses/bus_journey.py:330:39 [unsupported-operation] `None` is not subscriptable
houses/bus_journey.py:385:23 [not-iterable] Type `None` is not iterable
houses/bus_journey.py:400:19 [missing-attribute] Object of class `NoneType` has no attribute `get`
houses/commute_router.py:486:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
houses/commute_router.py:506:21 [missing-attribute] Object of class `NoneType` has no attribute `infeasible`
houses/commute_router.py:507:17 [missing-attribute] Object of class `NoneType` has no attribute `duration`
houses/commute_router.py:518:19 [no-matching-overload] No matching overload found for function `min` called with arguments: (Generator[Commute | None], key=(c: Commute) -> tuple[int, float])
houses/location.py:199:30 [bad-index] Cannot index into `dict[str, Any]`
houses/location.py:200:30 [bad-index] Cannot index into `dict[str, Any]`
houses/nodes/area.py:23:55 [missing-attribute] Object of class `NoneType` has no attribute `lat`
houses/nodes/area.py:23:64 [missing-attribute] Object of class `NoneType` has no attribute `lon`
houses/nodes/commute_pipeline_builder.py:178:33 [bad-argument-type] Argument `UserInputNode[str]` is not assignable to parameter `then_branch` with type `Node[Commute | str | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
houses/nodes/commute_pipeline_builder.py:202:33 [bad-argument-type] Argument `RailFareNode` is not assignable to parameter `then_branch` with type `Node[Commute | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
houses/nodes/life_insurance_total_node.py:45:18 [not-iterable] Type `None` is not iterable
houses/nodes/nearest_station_node.py:28:44 [bad-argument-type] Argument `GeoPoint | None` is not assignable to parameter `point` with type `GeoPoint` in function `houses.rail_fares.RailFareRegistry.nearest_station`
houses/nodes/rail_fare_node.py:94:43 [bad-argument-type] Argument `GeoPoint | None` is not assignable to parameter `point` with type `GeoPoint` in function `houses.rail_fares.RailFareRegistry.nearest_station`
houses/nodes/schools.py:48:29 [bad-assignment] `float` is not assignable to dict key `lat` with type `str | None`
houses/nodes/settings_node.py:37:41 [not-a-type] Expected a type form, got instance of `(obj: object, /) -> TypeIs[(...) -> object]`
houses/school.py:132:44 [bad-argument-type] Argument `Unknown | None` is not assignable to parameter `raw` with type `str` in function `School._try_int`
houses/school.py:133:45 [bad-argument-type] Argument `Unknown | None` is not assignable to parameter `raw` with type `str` in function `School._try_int`
houses/schools.py:104:20 [bad-return] Returned type `Attempt[GeoPoint] | Unknown` is not assignable to declared return type `Attempt[School | None]`
houses/schools.py:109:20 [bad-return] Returned type `Attempt[GeoPoint] | Unknown` is not assignable to declared return type `Attempt[School | None]`
houses/server.py:256:6 [bad-return] Function declared to return `JSONResponse | StreamingResponse`, but one or more paths are missing an explicit `return`
houses/server.py:306:37 [bad-assignment] `float` is not assignable to attribute `price` with type `Money | None`
houses/server.py:315:22 [bad-argument-type] Argument `int | None` is not assignable to parameter `bedrooms` with type `int` in function `houses.property.EnrichedProperty.__init__`
houses/server.py:316:19 [bad-argument-type] Argument `Money | float | None` is not assignable to parameter `price` with type `Money` in function `houses.property.EnrichedProperty.__init__`
houses/services.py:240:13 [missing-attribute] Object of class `Credentials` has no attribute `id_token`
houses/services.py:244:16 [bad-return] Returned type `Mapping[str, Any]` is not assignable to declared return type `dict[Unknown, Unknown]`
houses/sheets/formulas.py:151:44 [bad-argument-type] Argument `Literal['USER_ENTERED']` is not assignable to parameter `value_input_option` with type `ValueInputOption | None` in function `gspread.worksheet.Worksheet.update`
houses/sheets/named_ranges.py:68:66 [bad-argument-type] Argument `Literal['USER_ENTERED']` is not assignable to parameter `value_input_option` with type `ValueInputOption | None` in function `gspread.worksheet.Worksheet.update`
houses/sheets/view.py:76:44 [bad-argument-type] Argument `Literal['USER_ENTEred']` is not assignable to parameter `value_input_option` with type `ValueInputOption | None` in function `gspread.worksheet.Worksheet.update`
scripts/deploy_script.py:15:1 [missing-import] Cannot find module `googleapiclient.discovery`
scripts/migrate_settings.py:58:12 [bad-return] Returned type `int | None` is not assignable to declared return type `int`
scripts/parse_netex_fares.py:409:42 [bad-argument-type] Argument `str | None` is not assignable to parameter `x` with type `Buffer | SupportsFloat | SupportsIndex | str` in function `float.__new__`
scripts/parse_netex_fares.py:491:30 [bad-argument-type] Argument `str | None` is not assignable to parameter `x` with type `Buffer | SupportsFloat | SupportsIndex | str` in function `float.__new__`
scripts/parse_netex_fares.py:620:40 [unsupported-operation] Cannot set item in `dict[str, float]`
scripts/update_sheet.py:221:45 [bad-argument-type] Argument `list[int]` is not assignable to parameter `col_indices` with type `set[int]` in function `_fields_for_columns`
tests/integration/test_council_tax.py:148:16 [missing-attribute] Object of class `NoneType` has no attribute `band`
tests/integration/test_council_tax.py:149:16 [missing-attribute] Object of class `NoneType` has no attribute `yearly_cost`
tests/integration/test_council_tax.py:150:36 [missing-attribute] Object of class `NoneType` has no attribute `evidence_url`
tests/integration/test_council_tax.py:327:16 [missing-attribute] Object of class `NoneType` has no attribute `band`
tests/integration/test_council_tax.py:348:16 [missing-attribute] Object of class `NoneType` has no attribute `band`
tests/integration/test_council_tax.py:360:16 [missing-attribute] Object of class `NoneType` has no attribute `band`
tests/integration/test_council_tax.py:361:16 [missing-attribute] Object of class `NoneType` has no attribute `yearly_cost`
tests/integration/test_council_tax.py:362:16 [missing-attribute] Object of class `NoneType` has no attribute `evidence_url`
tests/integration/test_council_tax.py:401:16 [missing-attribute] Object of class `NoneType` has no attribute `band`
tests/unit/nodes/test_commute.py:1383:25 [bad-argument-type] Argument `DerivedNode[Commute]` is not assignable to parameter `then_branch` with type `Node[Commute | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
tests/unit/nodes/test_commute.py:1482:25 [bad-argument-type] Argument `TestFareConditionalDependency.test_drive_selection_never_activates_fare_dependency._WouldFailFare` is not assignable to parameter `then_branch` with type `Node[Commute | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
tests/unit/nodes/test_commute.py:1601:25 [bad-argument-type] Argument `TestFareConditionalDependency.test_transit_selection_activates_fare_dependency._Fare` is not assignable to parameter `then_branch` with type `Node[Commute | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
tests/unit/nodes/test_commute_pipeline.py:291:9 [missing-attribute] Object of class `RoutePlanner` has no attribute `routes`
tests/unit/nodes/test_commute_pipeline.py:366:30 [bad-argument-type] Argument `_FakeStationRegistry` is not assignable to parameter `station_registry` with type `StationRegistry | None` in function `houses.nodes.park_and_ride_augment_node.ParkAndRideAugmentNode.__init__`
tests/unit/nodes/test_commute_pipeline.py:372:31 [bad-argument-type] Argument `_FakeCarParkRegistry` is not assignable to parameter `car_park_registry` with type `CarParkRegistry | None` in function `houses.nodes.park_and_ride_augment_node.ParkAndRideAugmentNode.__init__`
tests/unit/nodes/test_commute_pipeline.py:418:9 [missing-attribute] Object of class `RoutePlanner` has no attribute `routes`
tests/unit/nodes/test_commute_pipeline.py:523:25 [bad-argument-type] Argument `FixedCommuteNode` is not assignable to parameter `then_branch` with type `Node[Commute | None]` in function `dag.if_then_else_node.IfThenElseNode.__init__`
tests/unit/nodes/test_property_json.py:344:13 [bad-argument-type] Argument `list[dict[str, bool | list[str] | str] | dict[str, bool | str]]` is not assignable to parameter `value` with type `list[Person]` in function `dag.user_input_node.UserInputNode.push`
tests/unit/nodes/test_property_json.py:377:13 [bad-argument-type] Argument `list[dict[str, bool | str]]` is not assignable to parameter `value` with type `list[Person]` in function `dag.user_input_node.UserInputNode.push`
tests/unit/nodes/test_schools.py:690:16 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/nodes/test_schools.py:690:91 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/nodes/test_schools.py:735:16 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/nodes/test_schools.py:736:41 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/nodes/test_schools.py:783:16 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/nodes/test_schools.py:881:16 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/test_bus_fares.py:276:16 [missing-attribute] Object of class `NoneType` has no attribute `get`
tests/unit/test_car_park.py:146:22 [missing-attribute] Object of class `NoneType` has no attribute `amount`
tests/unit/test_car_park.py:346:44 [bad-argument-type] Argument `CarPark | None` is not assignable to parameter `car_park` with type `CarPark` in function `houses.car_park.CarParkRegistry.load_costs`
tests/unit/test_car_park.py:372:16 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/test_car_park.py:373:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/test_commute_searches.py:154:26 [bad-argument-type] Argument `bool | float | int | str` is not assignable to parameter `lat` with type `float` in function `houses.geo.GeoPoint.__init__`
tests/unit/test_commute_searches.py:154:38 [bad-argument-type] Argument `bool | float | int | str` is not assignable to parameter `lon` with type `float` in function `houses.geo.GeoPoint.__init__`
tests/unit/test_enricher.py:161:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_enricher.py:162:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/test_enricher.py:305:16 [missing-attribute] Object of class `NoneType` has no attribute `person`
tests/unit/test_enricher.py:363:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/test_enricher.py:517:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_enricher.py:518:22 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/test_enricher.py:519:16 [missing-attribute] Object of class `NoneType` has no attribute `label`
tests/unit/test_enricher.py:544:31 [bad-argument-type] Argument `dict[str, UserInputNode[Commute]]` is not assignable to parameter `commute_selectors` with type `dict[str, Node[Unknown]]` in function `houses.nodes.commute_breakdown_node.CommuteBreakdownNode.__init__`
tests/unit/test_enricher.py:579:16 [unsupported-operation] `None` is not subscriptable
tests/unit/test_enricher.py:599:31 [bad-argument-type] Argument `dict[str, UserInputNode[Commute]]` is not assignable to parameter `commute_selectors` with type `dict[str, Node[Unknown]]` in function `houses.nodes.commute_breakdown_node.CommuteBreakdownNode.__init__`
tests/unit/test_enricher.py:643:16 [unsupported-operation] `None` is not subscriptable
tests/unit/test_enricher.py:664:16 [unsupported-operation] `None` is not subscriptable
tests/unit/test_enricher.py:684:31 [bad-argument-type] Argument `dict[str, UserInputNode[Commute]]` is not assignable to parameter `commute_selectors` with type `dict[str, Node[Unknown]]` in function `houses.nodes.commute_breakdown_node.CommuteBreakdownNode.__init__`
tests/unit/test_enricher.py:715:16 [unsupported-operation] `None` is not subscriptable
tests/unit/test_extract_bus_fares.py:43:41 [bad-argument-type] Argument `list[houses.stations.Station]` is not assignable to parameter `stations` with type `list[scripts.parse_netex_fares.Station]` in function `scripts.parse_netex_fares.parse_netex_fares`
tests/unit/test_extract_bus_fares.py:58:41 [bad-argument-type] Argument `list[houses.stations.Station]` is not assignable to parameter `stations` with type `list[scripts.parse_netex_fares.Station]` in function `scripts.parse_netex_fares.parse_netex_fares`
tests/unit/test_routing.py:126:12 [missing-attribute] Object of class `NoneType` has no attribute `name`
tests/unit/test_routing.py:229:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:244:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:269:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:294:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:318:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:359:16 [missing-attribute] Object of class `NoneType` has no attribute `daily_cost`
tests/unit/test_routing.py:418:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:419:46 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:466:16 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:467:48 [missing-attribute] Object of class `NoneType` has no attribute `duration`
tests/unit/test_routing.py:567:73 [bad-argument-type] Argument `TestSchoolCommute.test_delegates_to_get_commute._FakeRouter` is not assignable to parameter `router` with type `CommuteRouter | None` in function `houses.schools.compute_school_commute`
tests/unit/test_town_service.py:45:28 [bad-argument-type] Argument `(**k: Unknown) -> TestFindNearestTownName.test_returns_town_name._FakeCM` is not assignable to parameter `client_factory` with type `((...) -> AsyncClient) | None` in function `houses.location.find_nearest_town_name`
tests/unit/test_town_service.py:79:28 [bad-argument-type] Argument `(**k: Unknown) -> TestFindNearestTownName.test_no_features_returns_impossible._FakeCM` is not assignable to parameter `client_factory` with type `((...) -> AsyncClient) | None` in function `houses.location.find_nearest_town_name`
tests/unit/test_town_service.py:106:32 [bad-argument-type] Argument `(**k: Unknown) -> TestFindNearestTownName.test_re_raises_transient_http_error._FakeCM` is not assignable to parameter `client_factory` with type `((...) -> AsyncClient) | None` in function `houses.location.find_nearest_town_name`
tests/unit/test_town_service.py:141:28 [bad-argument-type] Argument `(**k: Unknown) -> TestGenerateTownDescription.test_returns_description._FakeCM` is not assignable to parameter `client_factory` with type `((...) -> AsyncClient) | None` in function `houses.town_desc.generate_town_description`
tools/capture_dom.py:403:16 [missing-attribute] Object of class `NoneType` has no attribute `strip`
tools/update_school_coords.py:164:43 [missing-attribute] Object of class `NoneType` has no attribute `lat`
tools/update_school_coords.py:165:44 [missing-attribute] Object of class `NoneType` has no attribute `lon`
dag/expression.py:228:38 [unsupported-operation]
dag/expression.py:248:38 [unsupported-operation]
dag/expression.py:272:38 [unsupported-operation]
dag/expression.py:305:38 [unsupported-operation]
dag/persistence.py:45:26 [invalid-argument]
dag/scheduler.py:199:5 [missing-attribute]
dag/signals.py:68:13 [not-callable]
dag/user_input_node.py:94:34 [invalid-argument]
houses/bus_journey.py:245:32 [missing-attribute]
houses/bus_journey.py:272:32 [missing-attribute]
houses/bus_journey.py:324:23 [not-iterable]
houses/bus_journey.py:327:39 [unsupported-operation]
houses/bus_journey.py:381:23 [not-iterable]
houses/bus_journey.py:396:19 [missing-attribute]
houses/location.py:193:30 [bad-index]
houses/location.py:194:30 [bad-index]
houses/nodes/area.py:21:55 [missing-attribute]
houses/nodes/area.py:21:64 [missing-attribute]
houses/nodes/commute_pipeline_builder.py:172:33 [bad-argument-type]
houses/nodes/commute_pipeline_builder.py:197:33 [bad-argument-type]
houses/nodes/life_insurance_node.py:45:18 [not-iterable]
houses/nodes/rail_fare_node.py:96:43 [bad-argument-type]
houses/nodes/schools.py:50:29 [bad-assignment]
houses/nodes/settings_node.py:36:41 [not-a-type]
houses/nodes/station.py:26:44 [bad-argument-type]
houses/routing.py:433:16 [missing-attribute]
houses/routing.py:459:21 [missing-attribute]
houses/routing.py:460:17 [missing-attribute]
houses/routing.py:470:19 [no-matching-overload]
houses/school.py:136:44 [bad-argument-type]
houses/school.py:137:45 [bad-argument-type]
houses/schools.py:93:20 [bad-return]
houses/schools.py:98:20 [bad-return]
houses/server.py:207:6 [bad-return]
houses/server.py:267:41 [bad-assignment]
houses/server.py:281:22 [bad-argument-type]
houses/server.py:282:19 [bad-argument-type]
houses/services.py:210:13 [missing-attribute]
houses/services.py:214:16 [bad-return]
houses/sheets/formulas.py:147:44 [bad-argument-type]
houses/sheets/named_ranges.py:70:66 [bad-argument-type]
houses/sheets/tab.py:46:56 [bad-argument-type]
houses/sheets/view.py:72:44 [bad-argument-type]
scripts/deploy_script.py:14:1 [missing-import]
scripts/migrate_settings.py:53:12 [bad-return]
scripts/parse_netex_fares.py:394:42 [bad-argument-type]
scripts/parse_netex_fares.py:475:30 [bad-argument-type]
scripts/parse_netex_fares.py:602:40 [unsupported-operation]
scripts/update_sheet.py:217:45 [bad-argument-type]
tests/integration/test_council_tax.py:93:20 [missing-attribute]
tests/integration/test_council_tax.py:94:20 [missing-attribute]
tests/integration/test_council_tax.py:95:40 [missing-attribute]
tests/integration/test_council_tax.py:274:20 [missing-attribute]
tests/integration/test_council_tax.py:297:20 [missing-attribute]
tests/integration/test_council_tax.py:309:20 [missing-attribute]
tests/integration/test_council_tax.py:310:20 [missing-attribute]
tests/integration/test_council_tax.py:311:20 [missing-attribute]
tests/integration/test_council_tax.py:352:20 [missing-attribute]
tests/unit/nodes/test_commute.py:1382:25 [bad-argument-type]
tests/unit/nodes/test_commute.py:1481:25 [bad-argument-type]
