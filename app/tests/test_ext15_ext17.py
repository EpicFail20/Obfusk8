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
"""
EXT-15: without docker-compose (tests, another deployment), the code default
of DEFAULT_SCORE_THRESHOLD is the doctrine's 0.4, the value docker-compose.yml
sets, not 0.5 (see the comment in main.py).
"""

import os

import main


def test_ext15_seuil_par_defaut_du_code_aligne_sur_la_doctrine():
    assert "DEFAULT_SCORE_THRESHOLD" not in os.environ, "the test must see the code default"
    assert main.DEFAULT_SCORE_THRESHOLD == 0.4
