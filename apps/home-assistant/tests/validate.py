"""Offline manifest regressions. Requires helm, kubectl and mikefarah/yq, no cluster."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "apps/home-assistant"
CHART = APP / "sources/chart"


def command(*args, input=None, check=True):
    return subprocess.run(args, cwd=ROOT, input=input, text=True, capture_output=True, check=check)


def documents(text):
    output = command("yq", "eval-all", "-o=json", "-I=0", ".", "-", input=text).stdout
    return [json.loads(line) for line in output.splitlines() if line.strip() and line != "null"]


def render(*args, check=True):
    return command("helm", "template", "home-assistant", str(CHART), "--namespace",
                   "home-assistant", "--kube-version", "1.35.0", *args, check=check)


class Manifests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = documents(render().stdout)
        cls.database = documents(command("kubectl", "kustomize", str(APP / "kustomization-db")).stdout)
        cls.resources = documents(command("kubectl", "kustomize", str(APP / "kustomization")).stdout)
        cls.deployments = {d["metadata"]["name"]: d for d in cls.rendered if d["kind"] == "Deployment"}
        cls.config = documents((CHART / "files/configuration.yaml").read_text())[0]

    def test_no_duplicate_resources(self):
        keys = [(d["apiVersion"], d["kind"], d["metadata"]["name"])
                for d in self.rendered + self.database + self.resources]
        self.assertEqual(len(keys), len(set(keys)))

    def test_security_and_pins(self):
        for deployment in self.deployments.values():
            pod = deployment["spec"]["template"]["spec"]
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertFalse(pod["hostNetwork"])
            self.assertTrue(pod["securityContext"]["runAsNonRoot"])
            self.assertEqual(pod["securityContext"]["fsGroup"], 3000)
            for container in pod.get("initContainers", []) + pod["containers"]:
                self.assertRegex(container["image"], r"^reg\.yiannis\.io/.+@sha256:[0-9a-f]{64}$")
                security = container["securityContext"]
                self.assertTrue(security["readOnlyRootFilesystem"])
                self.assertFalse(security["allowPrivilegeEscalation"])
                self.assertEqual(security["capabilities"]["drop"], ["ALL"])
            self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")

    def test_storage(self):
        pvcs = [d for d in self.rendered if d["kind"] == "PersistentVolumeClaim"]
        self.assertEqual(len(pvcs), 3)
        for pvc in pvcs:
            self.assertEqual(pvc["spec"]["storageClassName"], "truenas-apps")
            self.assertIn("Prune=false", pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"])
        db = next(d for d in self.database if d["kind"] == "Cluster")
        self.assertEqual(db["spec"]["storage"]["storageClass"], "truenas-cnpg")
        self.assertIn("@sha256:", db["spec"]["imageName"])

    def test_hardware_gate(self):
        self.assertEqual(self.deployments["home-assistant-zigbee2mqtt"]["spec"]["replicas"], 0)
        failed = render("--set", "controllers.zigbee2mqtt.replicas=1", check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Reserve the coordinator", failed.stderr)
        for address in ("192.168.88.0/24", "999.1.1.1"):
            failed = render("--set", "controllers.zigbee2mqtt.replicas=1",
                            "--set-string", f"hardware.coordinatorAddress={address}", check=False)
            self.assertNotEqual(failed.returncode, 0)
        # Documentation-only address: exercise rendering, never send traffic.
        candidate = documents(render("--set", "controllers.zigbee2mqtt.replicas=1",
                                     "--set-string", "hardware.coordinatorAddress=192.0.2.10").stdout)
        cnp = next(d for d in candidate if d["metadata"]["name"] == "home-assistant-zigbee-coordinator")
        self.assertEqual(cnp["spec"]["egress"][0]["toCIDR"], ["192.0.2.10/32"])

    def test_sso_and_history(self):
        self.assertEqual(self.config["homeassistant"]["auth_providers"], [{"type": "homeassistant"}])
        self.assertEqual(self.config["recorder"]["purge_keep_days"], 30)
        self.assertEqual(self.config["recorder"]["db_url"], "HA_DATABASE_URL")
        sso = self.config["auth_oidc"]
        self.assertFalse(sso["features"]["automatic_user_linking"])
        self.assertFalse(sso["features"]["default_redirect"])
        self.assertFalse(sso["features"]["disable_rfc7636"])
        self.assertTrue(sso["network"]["tls_verify"])
        self.assertEqual(sso["roles"]["user"], "home-assistant-users")
        self.assertTrue(self.config["prometheus"]["requires_auth"])

    def test_vendored_archive(self):
        archive = CHART / "files/vendor/hass-oidc-auth-v1.2.1.zip"
        expected = "e5badaaacaa63cfd6fe733924a05e76d75058836190398598fb24de57cd47ccd"
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), expected)
        cm = next(d for d in self.rendered if d["metadata"]["name"] == "home-assistant-sso")
        self.assertEqual(base64.b64decode(cm["binaryData"]["plugin.zip"]), archive.read_bytes())
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(json.loads(bundle.read("manifest.json"))["version"], "1.2.1")
            for name in bundle.namelist():
                self.assertFalse(name.startswith("/"))
                self.assertNotIn("..", Path(name).parts)
        self.assertTrue((CHART / "files/vendor/hass-oidc-auth-LICENSE.md").is_file())
        self.assertFalse(list((CHART / "files/vendor").glob("*.whl")))

    def test_services_and_monitoring(self):
        services = [d for d in self.rendered if d["kind"] == "Service"]
        for service in services:
            self.assertEqual(service["spec"]["type"], "ClusterIP")
            selector = service["spec"]["selector"]
            self.assertTrue(any(all(d["spec"]["template"]["metadata"]["labels"].get(k) == v
                                    for k, v in selector.items()) for d in self.deployments.values()))
        self.assertFalse(any(d["kind"] == "ServiceMonitor" for d in self.rendered))
        self.assertFalse(any(d["metadata"]["name"] == "home-assistant-prometheus-ingress"
                             for d in self.rendered))
        monitored = documents(render("--set", "metrics.serviceMonitorEnabled=true").stdout)
        monitor = next(d for d in monitored if d["kind"] == "ServiceMonitor")
        selector = monitor["spec"]["selector"]["matchLabels"]
        self.assertTrue(any(all(s["metadata"]["labels"].get(k) == v for k, v in selector.items())
                            for s in services))
        self.assertEqual(monitor["spec"]["endpoints"][0]["authorization"]["credentials"]["key"], "token")
        policy = next(d for d in monitored if d["metadata"]["name"] == "home-assistant-prometheus-ingress")
        self.assertEqual(policy["spec"]["ingress"][0]["fromEndpoints"][0]["matchLabels"], {
            "k8s:io.kubernetes.pod.namespace": "monitoring",
            "app.kubernetes.io/name": "prometheus",
        })

    def test_private_route_and_policy(self):
        route = next(d for d in self.resources if d["kind"] == "HTTPRoute")
        self.assertEqual(route["spec"]["parentRefs"][0]["sectionName"], "websecure-home")
        self.assertEqual(route["spec"]["hostnames"], ["home.yiannis.io"])
        self.assertNotIn("filters", route["spec"]["rules"][0])
        deny = next(d for d in self.database if d["metadata"]["name"] == "home-assistant-default-deny")
        self.assertEqual(deny["spec"], {"endpointSelector": {}, "ingress": [], "egress": []})
        for d in self.resources + self.database:
            if d["kind"] == "CiliumNetworkPolicy":
                self.assertNotIn('"world"', json.dumps(d))
        # Each application policy must match the actual rendered controller labels.
        for d in self.resources:
            if d["kind"] == "CiliumNetworkPolicy":
                labels = d["spec"]["endpointSelector"]["matchLabels"]
                self.assertTrue(any(all(dep["spec"]["template"]["metadata"]["labels"].get(k) == v
                                        for k, v in labels.items()) for dep in self.deployments.values()))

    def test_no_ca_private_key_in_ha(self):
        pod = self.deployments["home-assistant"]["spec"]["template"]["spec"]
        for volume in pod["volumes"]:
            if "secret" in volume:
                self.assertEqual(volume["secret"]["items"], [{"key": "ca.crt", "path": "ca.crt"}])

    def test_scoped_app_dns(self):
        policies = [d for d in self.resources + self.database if d["kind"] == "CiliumNetworkPolicy"]
        expected = {
            "home-assistant": {
                "authentik.yiannis.io",
                "home-assistant-database-rw.home-assistant.svc.cluster.local",
                "home-assistant-mqtt.home-assistant.svc.cluster.local",
            },
            "home-assistant-zigbee-mqtt": {"home-assistant-mqtt.home-assistant.svc.cluster.local"},
        }
        for name, names in expected.items():
            policy = next(d for d in policies if d["metadata"]["name"] == name)
            dns = [rule for e in policy["spec"]["egress"] for p in e.get("toPorts", [])
                   for rule in p.get("rules", {}).get("dns", [])]
            self.assertTrue(all(set(rule) == {"matchName"} for rule in dns))
            self.assertEqual({r["matchName"] for r in dns}, names)
        for policy in policies:
            for e in policy["spec"].get("egress", []):
                for p in e.get("toPorts", []):
                    if {"matchPattern": "*"} in p.get("rules", {}).get("dns", []):
                        self.assertEqual(policy["spec"]["endpointSelector"]["matchLabels"], {
                            "cnpg.io/cluster": "home-assistant-database"})
        for deployment in self.deployments.values():
            options = deployment["spec"]["template"]["spec"]["dnsConfig"]["options"]
            self.assertIn({"name": "ndots", "value": "1"}, options)

    def test_authentik_sni_and_no_idle_monitoring_ingress(self):
        policy = next(d for d in self.resources if d["metadata"]["name"] == "home-assistant")
        tls_ports = []
        for e in policy["spec"]["egress"]:
            for p in e.get("toPorts", []):
                if any(port["port"] in ("443", "8443") for port in p["ports"]):
                    self.assertEqual(p["serverNames"], ["authentik.yiannis.io"])
                    self.assertNotIn("rules", p)
                    tls_ports.extend(port["port"] for port in p["ports"])
        self.assertCountEqual(tls_ports, ["443", "8443"])
        for ingress in policy["spec"]["ingress"]:
            for endpoint in ingress.get("fromEndpoints", []):
                self.assertEqual(endpoint["matchLabels"]["k8s:io.kubernetes.pod.namespace"], "traefik")

    def test_manual_applications(self):
        for path in (APP / "applications").glob("*.yaml"):
            app = documents(path.read_text())[0]
            self.assertNotIn("automated", app["spec"]["syncPolicy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
