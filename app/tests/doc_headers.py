# Copyright (C) 2026 CARROLAGGI Xavier
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""Headers of a legitimate document-flow request, as Traefik forwards it
from the review interface: identity set by oauth2-proxy (EXT-47) and the
application's own origin (D-048 point 3). Fictitious values only."""

TEST_ORIGIN = "https://obfusk8.test.invalid"


def doc_headers(email: str = "alice@exemple.invalid") -> dict[str, str]:
    return {"x-auth-request-email": email, "origin": TEST_ORIGIN}
