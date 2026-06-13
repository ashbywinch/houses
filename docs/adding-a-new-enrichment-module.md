# Adding a New Enrichment Module

1. **Create the module** in `houses/` (e.g. `houses/my_module.py`). Accept
   the minimum input needed (postcode, address, or coordinates) and return
   structured data using ``Attempt[T]`` or a simple dataclass/dict.

2. **Add a protocol** to `houses/services.py` if the module is an external
   service that could be faked in tests.  Follow the existing protocol
   pattern (e.g. ``EPCLookupService``, ``CouncilTaxService``).

3. **Add a default implementation** in `houses/services.py` that wraps the
   real module (e.g. ``_DefaultEPCLookup`` calls ``lookup_epc()``).  Wire
   it into the ``Services`` dataclass ``field(default_factory=...)``.

4. **Wire into `_run_enrichment`** in `houses/server.py`.  Call your module
   through the services container:

   ```python
   result = await svc.my_service.lookup(...)
   ```

5. **Add columns** to `Row.HEADERS` in `houses/sheets/row.py` and update
   ``Row.from_property()`` to format the new fields.

6. **Run `POST /sync-view-formulas`** if the View tab needs new XLOOKUP
   formulas.

7. **Add a fake** in `tests/helpers.py` following the existing pattern
   (e.g. ``FakeEPC``, ``FakeCouncilTax``).  Make sure ``make_services()``
   passes the new fake by default.

8. **Write tests** using the DI patterns in `tests/helpers.py`:

   ```python
   from tests.helpers import make_services, FakeMyService

   services = make_services(my_service=FakeMyService(result=...))
   result = await _run_enrichment(..., services=services)
   ```

Follow the pattern of existing modules: fail gracefully (log warning,
return None/default on errors), use the shared cache infrastructure, and
add config fields to `houses/config.py` if new API keys or settings are
needed.
