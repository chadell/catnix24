import os
# import requests
import json
from django.contrib.contenttypes.models import ContentType
from nautobot.apps.jobs import Job, register_jobs, IntegerVar, ObjectVar
from nautobot.tenancy.models import Tenant
from nautobot.extras.models import Contact, CustomField, Role, Status, ContactAssociation
from nautobot.ipam.models import Namespace, VLAN, Prefix, IPAddress
from nautobot.dcim.models import LocationType, Location, Manufacturer, DeviceType, Device
from nautobot.extras.choices import CustomFieldTypeChoices
from nautobot_bgp_models.models import AutonomousSystem, BGPRoutingInstance, Peering, PeerEndpoint

CATNIX_PEERING_JSON = "https://www.catnix.net/wp-content/uploads/participants.json"


class LoadCATNIXData(Job):
    """Job that loads basic CATNIX info for a specific member."""

    member_asn = IntegerVar()

    class Meta:
        name = "Create CATNIX base data for a specific member"
        description = "Create CATNIX base data for a specific member"

    def run(self, member_asn):
        # response = requests.get(CATNIX_PEERING_JSON)
        # response.raise_for_status()
        # data = response.json()
        current_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(current_dir, "catnix.json"), "r") as catnix_json:
            data = json.load(catnix_json)

        # LOAD BASE CATNIX INFO
        ixp_info = data["ixp_list"][0]

        tenant, _ = Tenant.objects.get_or_create(
            name=ixp_info["shortname"],
            description=ixp_info["name"],
        )
        contact, _ = Contact.objects.get_or_create(
            name=ixp_info["shortname"],
            email=ixp_info["support_email"],
            phone=ixp_info["support_phone"],
        )
        support_role, _ = Role.objects.get_or_create(name="Support")
        support_role.content_types.set([ContentType.objects.get_for_model(ContactAssociation)])
        ContactAssociation.objects.get_or_create(
            contact=contact,
            status=Status.objects.get(name="Active"),
            associated_object_id=tenant.pk,
            associated_object_type=ContentType.objects.get_for_model(Tenant),
            role=support_role
        )

        namespace, _  =  Namespace.objects.get_or_create(
            name=ixp_info["shortname"],
        )
        for vlan_info in ixp_info["vlan"]:
            vlan, _ = VLAN.objects.get_or_create(
                vid=int(vlan_info["id"]),
                name=vlan_info["name"],
                status=Status.objects.get(name="Active"),
                tenant=tenant,
            )
            Prefix.objects.get_or_create(
                network=vlan_info["ipv4"]["prefix"],
                prefix_length=vlan_info["ipv4"]["mask_length"],
                ip_version=4,
                status=Status.objects.get(name="Active"),
                tenant=tenant,
                vlan=vlan,
                namespace=namespace,
            )
            Prefix.objects.get_or_create(
                network=vlan_info["ipv6"]["prefix"],
                prefix_length=vlan_info["ipv6"]["mask_length"],
                ip_version=6,
                status=Status.objects.get(name="Active"),
                tenant=tenant,
                vlan=vlan,
                namespace=namespace,
            )

        region_type, _ = LocationType.objects.get_or_create(name="Region")
        site_type, _ = LocationType.objects.get_or_create(name="Site", parent=region_type)
        manufacturer, _ = Manufacturer.objects.get_or_create(name="Arista")
        device_type, _ = DeviceType.objects.get_or_create(
            model="DCS-7150S-24",
            manufacturer=manufacturer
        )

        cf, _ = CustomField.objects.get_or_create(key="switch_id", defaults={
            "type": CustomFieldTypeChoices.TYPE_INTEGER,
            "label": "Switch ID",
        })
        cf.content_types.set([ContentType.objects.get_for_model(Device)])

        spain, _ = Location.objects.get_or_create(
            name="Spain",
            location_type=region_type,
            status=Status.objects.get(name="Active")
        )
        peering_fabric, _ = Role.objects.get_or_create(name="PeeringFabric")
        peering_fabric.content_types.set([ContentType.objects.get_for_model(Device)])

        for location_info in ixp_info["switch"]:
            location, _ = Location.objects.get_or_create(
                name=location_info["name"],
                parent=spain,
                status=Status.objects.get(name="Active"),
                location_type=site_type,
            )
            device, _ = Device.objects.get_or_create(
                name=location_info["name"],
                location=location,
                tenant=tenant,
                device_type=device_type,
                status=Status.objects.get(name="Active"),
                role=peering_fabric
            )
            device.cf["switch_id"] = location_info["id"]
            device.save()

        members = data["member_list"]

        edge, _ = Role.objects.get_or_create(name="Edge")
        edge.content_types.set([ContentType.objects.get_for_model(Device)])

        asn_status, _ = Status.objects.get_or_create(name="Remote")
        asn_status.content_types.add(ContentType.objects.get_for_model(AutonomousSystem))

        member_role, _ = Role.objects.get_or_create(name="Member")
        member_role.content_types.set([ContentType.objects.get_for_model(ContactAssociation)])


        for member in members:
            contact, _ = Contact.objects.get_or_create(
                name=member["name"],
                email=member["contact_email"][0],
                phone=member["contact_hone"][0],
            )
            ContactAssociation.objects.get_or_create(
                contact=contact,
                status=Status.objects.get(name="Active"),
                associated_object_id=tenant.pk,
                associated_object_type=ContentType.objects.get_for_model(Tenant),
                role=member_role
            )

            if member_asn == member["asnum"]:
                user_tenant, _ = Tenant.objects.get_or_create(
                    name=member["name"],
                )

                for connection in member["connection_list"]:
                    switch_id = connection["if_list"][0]["switch_id"]
                    peering_device = Device.objects.get(_custom_field_data__switch_id=switch_id)

                    mgmt_ipv4 = IPAddress.objects.create(
                        address=connection["vlan_list"][0]["ipv4"]["address"]+"/32",
                        namespace=namespace,
                        status=Status.objects.get(name="Active")

                    )

                    mgmt_ipv6  = IPAddress.objects.create(
                        address=connection["vlan_list"][0]["ipv6"]["address"]+"/128",
                        namespace=namespace,
                        status=Status.objects.get(name="Active"),
                    )

                    device, _ = Device.objects.get_or_create(
                        name=member["name"],
                        location=peering_device.location,
                        tenant=user_tenant,
                        device_type=device_type,
                        status=Status.objects.get(name="Active"),
                        role=edge,
                        primary_ip4=mgmt_ipv4,
                        primary_ip6=mgmt_ipv6,
                    )

                    asn, _ = AutonomousSystem.objects.update_or_create(
                        asn=member_asn,
                        defaults={
                            "description":member["name"],
                            "status": Status.objects.get(name="Active")
                        }
                    )

                    BGPRoutingInstance.objects.create(
                        autonomous_system=asn,
                        device=device,
                        status=Status.objects.get(name="Active"),
                    )

                self.logger.info("The member %s has been located in %s", member["name"], peering_device.location.name)
            else:
                AutonomousSystem.objects.get_or_create(
                    asn=member["asnum"],
                    status=asn_status,
                    description=member["name"]
                )


class RequestPeeringCATNIX(Job):
    """Job that connects CATNIX peerings."""

    my_asn = ObjectVar(
        model=AutonomousSystem,
        query_params={
            "status": "Active"
        },
    )
    remote_asn = ObjectVar(
        model=AutonomousSystem,
        query_params={
            "status": "Remote"
        },
    )

    class Meta:
        name = "Create peerings in CATNIX"
        description = "Create peerings in CATNIX"

    def run(self, my_asn, remote_asn):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(current_dir, "catnix.json"), "r") as catnix_json:
            data = json.load(catnix_json)


        namespace = Namespace.objects.get(
            name=data["ixp_list"][0]["shortname"],
        )
        members = data["member_list"]

        for member in members:
            if remote_asn.asn == member["asnum"]:
                for connection in member["connection_list"]:
                    routing_instance = my_asn.bgproutinginstance_set.first()
                    peering_v4 = Peering.objects.create(status=Status.objects.get(name="Active"))

                    PeerEndpoint.objects.create(
                        source_ip=routing_instance.device.primary_ip4,
                        peering=peering_v4,
                        routing_instance=routing_instance
                    )

                    remote_ipv4  = IPAddress.objects.create(
                        address=connection["vlan_list"][0]["ipv4"]["address"]+"/32",
                        namespace=namespace,
                        status=Status.objects.get(name="Active"),
                    )

                    PeerEndpoint.objects.create(
                        source_ip=remote_ipv4,
                        peering=peering_v4,
                        autonomous_system=remote_asn
                    )

                    peering_v6 = Peering.objects.create(status=Status.objects.get(name="Active"))

                    PeerEndpoint.objects.create(
                        source_ip=routing_instance.device.primary_ip6,
                        peering=peering_v6,
                        routing_instance=routing_instance
                    )

                    remote_ipv6  = IPAddress.objects.create(
                        address=connection["vlan_list"][0]["ipv6"]["address"]+"/128",
                        namespace=namespace,
                        status=Status.objects.get(name="Active"),
                    )

                    PeerEndpoint.objects.create(
                        source_ip=remote_ipv6,
                        peering=peering_v6,
                        autonomous_system=remote_asn
                    )

                self.logger.info("Peerings between %s defined", remote_asn)
                break

name = "CATNIX Jobs"
register_jobs(LoadCATNIXData, RequestPeeringCATNIX)
