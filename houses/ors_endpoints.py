"""HeiGIT endpoint URLs — one owner for every HeiGIT service we call.

HeiGIT unified its APIs under ``api.heigit.org/<service>/<version>/`` and
deprecated ``api.openrouteservice.org`` (announced 2026-04-28). Since
2026-08-27 the deprecated host carries **10% of the quota**, and its usage
is *not* shown in the account dashboard — only the new host's is
(announcement follow-up, 2026-09-15). That is exactly why the 2026-10-03
direction failures were unattributable: our calls went to the deprecated
host (reduced, separately accounted quota) while the dashboard showed the
new host's untouched pools.

The deprecated host is announced for shutdown 2026-11-02..06. The host is a
setting (``HOUSES_ORS_BASE_URL``) so the next move is a config change, and
the paths live here, once, instead of as literals in every caller.

Source (migration table): https://ask.openrouteservice.org/t/7912
"""

from houses.settings import settings

ORS_BASE = f"{settings.ors_base_url}/openrouteservice"
PELIAS_BASE = f"{settings.ors_base_url}/pelias/v1"

ORS_DIRECTIONS = f"{ORS_BASE}/v2/directions"
ORS_MATRIX = f"{ORS_BASE}/v2/matrix"
PELIAS_SEARCH = f"{PELIAS_BASE}/search"
PELIAS_REVERSE = f"{PELIAS_BASE}/reverse"
