#    Eve W-Space
#    Copyright (C) 2013  Andrew Austin and other contributors
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version. An additional term under section
#    7 of the GPL is included in the LICENSE file.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
from celery import task
from Map.models import System, KSystem, Signature, ActivePilot
from core.models import Faction, SystemJump
from core.models import Alliance
import eveapi
from API import utils as handler
from django.core.cache import cache
from datetime import datetime, timedelta
import pytz

@task()
def update_system_stats():
    """
    Updates the System Statistics (jumps, kills) from the API.
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    jumpapi = api.map.Jumps()
    killapi = api.map.Kills()
    System.objects.all().update(shipkills=0, podkills=0, npckills=0)
    KSystem.objects.all().update(jumps=0)
    # Update jumps from Jumps API for K-space systems
    for entry in jumpapi.solarSystems:
        try:
            sys = KSystem.objects.get(pk=entry.solarSystemID)
            sys.jumps = entry.shipJumps
            sys.save()
        except:
            pass
    # Update kills from Kills API
    for entry in killapi.solarSystems:
        try:
            sys = System.objects.get(pk=entry.solarSystemID)
            sys.shipkills = entry.shipKills
            sys.podkills = entry.podKills
            sys.npckills = entry.factionKills
            sys.save()
        except:
            pass

@task()
def update_system_sov():
    """
    Updates the Sov for K-Space systems. If any exceptions are raised
    (e.g. Alliance record doesn't exist), sov is just marked "None."
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    sovapi = api.map.Sovereignty()
    KSystem.objects.all().update(sov="None")
    for sys in sovapi.solarSystems:
        try:
            system = KSystem.objects.get(pk=sys.solarSystemID)
            if sys.factionID:
                system.sov = Faction.objects.get(pk=sys.factionID).name
                system.save()
            elif sys.allianceID:
                system.sov = Alliance.objects.get(pk=sys.allianceID).name
                system.save()
        except:
            pass

@task()
def fill_jumps_cache():
    """
    Ensures that the jumps cache is filled.
    """
    if not cache.get('sysJumps'):
        for sys in KSystem.objects.all():
            cache.set(sys.pk, [i.tosystem for i in SystemJump.objects.filter(
                fromsystem=sys.pk).all()])
        cache.set('sysJumps', 1)

@task()
def check_server_status():
    """
    Checks the server status, if it detects the server is down, set updated=False
    on all signatures.
    """
    api = eveapi.EVEAPIConnection(cacheHandler=handler)
    try:
        statusapi = api.server.ServerStatus()
    except:
        # API is down, assume the server is down as well
        Signature.objects.all().update(updated=False)
        return
    if statusapi.serverOpen == u'True':
        return
    Signature.objects.all().update(updated=False)

@task()
def downtime_site_update():
    """
    This task should be run during the scheduled EVE downtime.
    It triggers the increment_downtime function on all singatures
    that have been activated.
    """
    for sig in Signature.objects.all():
        if sig.downtimes or sig.downtimes == 0:
            sig.increment_downtime()

@task()
def clear_stale_locations():
    """
    This task will clear any user location records older than 15 minutes.
    """
    limit = datetime.now(pytz.utc) - timedelta(minutes=15)
    for record in ActivePilot.objects.all():
        if record.timestamp < limit:
            record.delete()
