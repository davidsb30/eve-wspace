from django.db import models
from django.contrib.auth.models import User, Group
from core.models import SystemData
from django import forms
from django.forms import ModelForm
import autocomplete_light as ac
from datetime import datetime
import pytz
# Create your models here.

class WormholeType(models.Model):
    """Stores the permanent information on wormhole types.
    Any changes to this table should be made with caution.

    """
    name = models.CharField(max_length = 4, unique = True)
    maxmass = models.BigIntegerField()
    jumpmass = models.BigIntegerField()
    lifetime = models.IntegerField()
    # source is a  2-char fields that can be:
    # 1-6 : Wormhole classes 1-6
    # H : High Sec Only (e.g. freighter capable K > C5 in high)
    # NH : Low or Nullsec (e.g. cap capable K > C5)
    # K : Any K-space (e.g. X702 to a C3)
    # N: Nullsec
    # L: Lowsec
    # Z: Class 5 or 6 (5/6 > K holes)
    # X: Special ID for K162 and stargates
    source = models.CharField(max_length = 2)
    # Destination is an integer representation of System.sysclass
    # Except that it may be 0 which indicates a K162 or Stargate
    destination = models.IntegerField()
    target = models.CharField(max_length = 15)

    def __unicode__(self):
        """Returns Wormhole ID as unicode representation."""
        return self.name


class System(SystemData):
    """Stores the permanent record of a solar system. 
    This table should not have rows added or removed through Django.

    """
    sysclass_choices = ((1, "C1"), (2, "C2"), (3, "C3"), (4, "C4"), (5, "C5"),
            (6, "C6"), (7, "High Sec"), (8, "Low Sec"), (9, "Null Sec"))
    sysclass = models.IntegerField(choices = sysclass_choices)
    occupied = models.TextField(blank = True)
    info = models.TextField(blank = True)
    lastscanned = models.DateTimeField()
    npckills = models.IntegerField(null=True, blank=True)
    podkills = models.IntegerField(null=True, blank=True)
    shipkills = models.IntegerField(null=True, blank=True)

    def __unicode__(self):
        """Returns name of System as unicode representation"""
        return self.name

    def is_kspace(self):
        return self.sysclass >= 7
    def is_wspace(self):
        return self.sysclass < 7

class KSystem(System):
    sov = models.CharField(max_length = 100)
    jumps = models.IntegerField(blank=True, null=True)


class WSystem(System):
    static1 = models.ForeignKey(WormholeType, blank=True, null=True, related_name="primary_statics")
    static2 = models.ForeignKey(WormholeType, blank=True, null=True, related_name="secondary_statics")
    effect = models.CharField(max_length=50, blank=True, null=True)

class Map(models.Model):
    """Stores the maps available in the map tool. root relates to System model."""
    name = models.CharField(max_length = 100, unique = True)
    root = models.ForeignKey(System, related_name="root")
    # Maps with explicitperms = True require an explicit permission entry to access.
    explicitperms = models.BooleanField()

    class Meta:
        permissions = (("map_unrestricted", "Do not require excplicit access to maps."),)

    def __unicode__(self):
        """Returns name of Map as unicode representation."""
        return self.name

    def __contains__(self, system):
        """
        A convience to allow 'if system in map:' type statements to determine if
        there exist a MapSystem with map == map and system == system.
        NOTE: system must be a System, NOT a MapSystem
        """
        #I *think* this should be handled by the filter used in the main return
        #statement, but as I require this behaviour I'll make it explicit
        if system is None:
            return False

        return self.systems.filter(system=system).exists()

    #Given a __contains__ function I guess it makes sense to implement this
    #so for ... in ... will work too.
    def __iter__(self):
        """
        Returns an iterator over all Systems in the map
        NOTE: returns Systems not MapSystems
        """
        for msys in self.systems.all():
            yield msys.system

    def add_log(self, user, action):
        """
        Adds a log entry into a MapLog for the map.
        """
        log = MapLog(user=user, map=self, action=action,
                     timestamp=datetime.now(pytz.utc))
        log.save()

    def get_permission(self, user):
        """
        Returns the highest permision that user has on the map.
        0 = No Access
        1 = Read Access
        2 = Write Access
        """
        #Special case: if user is unrestricted we don't care unless the map
        #requires explicit permissions
        if user.has_perm['Map.map_unrestricted'] and not self.explicitperms:
            return 2

        #Otherwise take the highest of the permissions granted by the groups
        #user is a member of
        highestperm = 0
        #query set of all groups the user is a member of
        groups = user.groups.all()
        #Done this way there should only be one db hit which gets all relevant
        #permissions
        for perm in self.grouppermissions.filter(group__in=groups):
            highestperm = max(highestperm, perm)

        return highestperm

    def add_system(self, user, system, friendlyname, parent=None):
        """
        Adds the provided system to the map with the provided
        friendly name. Returns the new MapSystem object. 
        """
        system = MapSystem(map=self, system=system, friendlyname=friendlyname, parentsystem = parent)
        system.save()
        self.add_log(user, "Added system: %s" % system.name)
        return system

class MapSystem(models.Model):
    """Stores information regarding which systems are active in which maps at the present time."""
    map = models.ForeignKey(Map, related_name="systems")
    system = models.ForeignKey(System, related_name="maps")
    friendlyname = models.CharField(max_length = 10)
    interesttime = models.DateTimeField(null=True, blank=True)
    parentsystem = models.ForeignKey('self', related_name="childsystems", 
            null=True, blank=True)
    def __unicode__(self):
        return "system %s in map %s" % (self.system.name, self.map.name)

    def connect_to(self, system,
                   topType, bottomType, 
                   topBubbled=False, bottomBubbled=False,
                   timeStatus=0, massStatus=0):
        """
        Connect self to system and add the connecting WH to map, self is the
        bottom system. Returns the connecting wormhole.
        """
        wormhole = Wormhole(map=self.map, top = system, bottom=self,
                      top_type=topType, bottom_type=bottomType,
                      top_bubbled=topBubbled, bottom_bubbled=bottomBubbled,
                      time_status=timeStatus, mass_status=massStatus)

        wormhole.save()
        return wormhole

class Wormhole(models.Model):
    """An instance of a wormhole in a  map. 
    Wormhole have a 'top' and a 'bottom', the top refers to the 
    side that is found first (and the bottom is obviously the other side)

    """
    map = models.ForeignKey(Map, related_name='wormholes')
    top = models.ForeignKey(MapSystem, related_name='child_wormholes')
    top_type = models.ForeignKey(WormholeType, related_name='+')
    top_bubbled = models.NullBooleanField(null=True, blank=True)
    bottom = models.ForeignKey(MapSystem, null=True, related_name='parent_wormholes') 
    bottom_type = models.ForeignKey(WormholeType, related_name='+')
    bottom_bubbled = models.NullBooleanField(null=True, blank=True)
    time_status = models.IntegerField(choices = ((0, "Fine"), (1, "End of Life")))
    mass_status = models.IntegerField(choices = ((0, "No Shrink"), 
        (1, "First Shrink"), (2, "Critical")))


class SignatureType(models.Model):
    """Stores the list of possible signature types for the map tool. 
    Custom signature types may be added at will.

    """
    shortname = models.CharField(max_length = 6)
    longname = models.CharField(max_length = 100)
    # sleepersite and escalatable are used to track wormhole comabt sites.
    # sleepersite = true should give a "Rats cleared" option
    # escalatable = true should cause escalation tracking to kick in in C5/6
    sleeprsite = models.BooleanField()
    escalatable = models.BooleanField()

    def __unicode__(self):
        """Returns short name as unicode representation"""
        return self.shortname


class Signature(models.Model):
    """Stores the signatures active in all systems. Relates to System model."""
    system = models.ForeignKey(System, related_name="signatures")
    sigtype = models.ForeignKey(SignatureType, related_name="sigs")
    sigid = models.CharField(max_length = 10)
    # TODO: Implement server status checker to reset updated and increment
    #       downtimes
    updated = models.BooleanField()
    info = models.CharField(max_length=65, null=True, blank=True)
    # ratscleared and lastescalated are used to track wormhole combat sites.
    # ratscleared is the DateTime that sleepers were cleared initially
    # lastescalated is the last time the site was escalated (if applicable)
    # activated is when the site was marked activated for reference
    # downtimes should be incremented by the server status checker
    activated = models.DateTimeField(null=True, blank=True)
    downtimes = models.IntegerField(null=True, blank=True)
    ratscleared = models.DateTimeField(null=True, blank=True)
    lastescalated = models.DateTimeField(null=True, blank=True)

    def __unicode__(self):
        """Returns sig ID as unicode representation"""
        return self.sigid


class MapPermission(models.Model):
    """Relates a user group to it's map permissions. Non-restricted groups will have change access to all maps."""
    group = models.ForeignKey(Group, related_name="mappermissions")
    map = models.ForeignKey(Map, related_name="grouppermissions")
    access = models.IntegerField(choices=((0,'No Access'),
        (1,'View Only'), (2,'View / Change')))


class MapLog(models.Model):
    """Represents an action that has taken place on a map (e.g. adding a signature). 
    This is used for pushing updates since last page load to clients.

    """
    map = models.ForeignKey(Map, related_name="logentries")
    user = models.ForeignKey(User, related_name="maplogs")
    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=255)

    def __unicode__(self):
        return "Map: %s  User: %s  Action: %s  Time: %s" % (self.map.name, self.user.username, 
                self.action, self.timestamp)


class Snapshot(models.Model):
    """Represents a snapshot of the JSON strings that are used to draw a map."""

    mapname = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, related_name='snapshots')
    systemsjson = models.TextField()
    wormholesjson = models.TextField()


# Model Forms
class MapForm(ModelForm):
    class Meta:
        model = Map


class SignatureForm(ModelForm):
    """This form should only be used with commit=False since it does not
    set the system or updated fields.

    """
    sigid = forms.CharField(widget=forms.TextInput(attrs={'class': 'input-mini'}), label="ID:")
    sigtype = forms.ModelChoiceField(queryset=SignatureType.objects.all(), label="Type:")
    info = forms.CharField(widget=forms.TextInput(attrs={'class': 'input-medium'}),label="Info:")
    sigtype.widget.attrs['class'] = 'input-small'
    class Meta:
        model = Signature
        fields = ('sigid', 'sigtype', 'info')

