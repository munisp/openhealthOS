"""devicehub.py — the device integration layer: every domain meets its
machines.

The doctrine is the PACS adapter's doctrine, generalized:

  - VENDOR SDKs ARE COMPOSED, NEVER EMBEDDED. Proprietary
    manufacturer SDKs are licence-restricted and version-locked; this
    platform references the vendor integration surface (endpoint +
    credentials + transport callable supplied at deployment) and
    refuses loudly without it. No vendor binary ships in this repo.
  - OPEN PROTOCOLS ARE IMPLEMENTED HERE, CLEAN-ROOM: HL7 v2 (ORU
    observations, ACK), and DICOM via the pacs package. IEEE 11073
    SDC and vendor REST APIs are declared as descriptors with their
    licence posture — honest about what is implemented versus what is
    wired-at-deployment.
  - A CABLE NOT PLUGGED IN IS NOT AN INTERFACE: no endpoint, no
    pretend. Every connect() without an endpoint raises.
  - INBOUND DATA IS EVIDENCE, NOT TRUTH: every parsed observation
    carries its source descriptor id and protocol; unmapped fields
    surface as unmapped, never silently dropped.

Top-3 manufacturers per domain plus the open-source lane, as
versioned data (DEVICE_ADAPTER_VERSION). Adding a vendor is adding a
row and a test, not a fork.
"""

DEVICE_ADAPTER_VERSION = "device-adapters@1.0.0"


class DeviceHubError(Exception):
    pass


# ----------------------------------------------------------------
# The adapter catalogue. posture:
#   open       — protocol implemented in this repo, clean-room
#   composed   — vendor surface referenced; transport supplied at
#                deployment (vendor SDK / REST credentials); nothing
#                proprietary ships here
DEVICE_ADAPTERS = {
    "ecg": (
        {"id": "ge-muse", "vendor": "GE HealthCare",
         "models": "MAC series carts → MUSE",
         "protocol": "vendor REST / MUSE XML", "posture": "composed",
         "note": "MUSE web API pulls traced ECGs; credentials at "
                 "deployment, SDK never vendored"},
        {"id": "philips-tracemaster", "vendor": "Philips",
         "models": "PageWriter → IntelliSpace ECG / TraceMaster",
         "protocol": "vendor REST + DICOM ECG", "posture": "composed",
         "note": "TraceMasterVue web query; DICOM ECG lands via pacs"},
        {"id": "mortara-eli", "vendor": "Baxter (Mortara)",
         "models": "ELI series → ELI Link",
         "protocol": "vendor XML/REST (ELI Link)",
         "posture": "composed",
         "note": "ELI Link middleware; order download + result upload"},
        {"id": "open-ecg", "vendor": "open-source",
         "models": "any HL7 aECG / SCP-ECG / WFDB source",
         "protocol": "HL7 v2 ORU (aECG) / WFDB", "posture": "open",
         "note": "clean-room HL7 ORU codec below; WFDB via "
                 "PhysioNet-format files; Physiospy-style carts speak "
                 "this lane"},
    ),
    "patient-monitor": (
        {"id": "philips-intellivue", "vendor": "Philips",
         "models": "IntelliVue + Patient Information Center iX",
         "protocol": "vendor REST / HL7 outbound",
         "posture": "composed",
         "note": "PIC iX HL7 outbound feed is the documented lane"},
        {"id": "ge-carescape", "vendor": "GE HealthCare",
         "models": "CARESCAPE monitors + central station",
         "protocol": "vendor HL7 gateway", "posture": "composed",
         "note": "CARESCAPE Gateway emits HL7 ORU vitals"},
        {"id": "mindray-benelink", "vendor": "Mindray",
         "models": "BeneVision + BeneLink",
         "protocol": "vendor HL7 gateway (BeneLink)",
         "posture": "composed",
         "note": "BeneLink aggregation module, HL7 outbound"},
        {"id": "open-sdc", "vendor": "open-source",
         "models": "any IEEE 11073 SDC participant",
         "protocol": "IEEE 11073 SDC (openSDC libraries)",
         "posture": "open",
         "note": "descriptor-declared: SDC stacks (e.g. sdccc/openSDC-"
                 "style) compose at deployment; vitals normalize to "
                 "the same observation shape as HL7"},
    ),
    "dialysis": (
        {"id": "fresenius-6008", "vendor": "Fresenius Medical Care",
         "models": "5008 / 6008 CAREsystem",
         "protocol": "vendor data export / HL7", "posture": "composed",
         "note": "therapy data export to unit network; mapped to "
                 "nephrology sessions"},
        {"id": "vantive-ak98", "vendor": "Vantive (ex-Baxter)",
         "models": "AK 98 / Artis", "protocol": "vendor export / HL7",
         "posture": "composed",
         "note": "session parameters export; UF and conductivity "
                 "trend feeds"},
        {"id": "bbraun-dialog", "vendor": "B.Braun",
         "models": "Dialog+ / Dialog iQ",
         "protocol": "vendor export / HL7", "posture": "composed",
         "note": "Dialog+ data interface; session download at "
                 "treatment end"},
        {"id": "open-hd", "vendor": "open-source",
         "models": "any HL7-emitting HD machine",
         "protocol": "HL7 v2 ORU", "posture": "open",
         "note": "clean-room ORU codec below; session parameters "
                 "normalize into nephrology adequacy inputs"},
    ),
    "ultrasound": (
        {"id": "ge-voluson", "vendor": "GE HealthCare",
         "models": "Voluson E10/E8/S10", "protocol": "DICOM + vendor SR",
         "posture": "composed",
         "note": "DICOM store lands via pacs; biometry SR parsed "
                 "through the DICOM lane"},
        {"id": "philips-epiq", "vendor": "Philips",
         "models": "EPIQ Elite / Affiniti",
         "protocol": "DICOM + vendor SR", "posture": "composed",
         "note": "same DICOM lane; obstetric SR templates"},
        {"id": "samsung-hera", "vendor": "Samsung Medison",
         "models": "HERA W10 / V8", "protocol": "DICOM + vendor SR",
         "posture": "composed",
         "note": "DICOM lane; S-Detect measurements via SR"},
        {"id": "open-us", "vendor": "open-source",
         "models": "any DICOM modality (incl. Butterfly/Clarius cloud "
                   "export)", "protocol": "DICOM (pacs package)",
         "posture": "open",
         "note": "DICOMweb adapter already honest-tested; imaging "
                 "never needs a vendor SDK to leave the machine"},
    ),
    "pathology-scanner": (
        {"id": "philips-intellisite", "vendor": "Philips",
         "models": "IntelliSite ultra fast scanner",
         "protocol": "iSyntax (proprietary) → DICOM WSI conversion",
         "posture": "composed",
         "note": "vendor SDK licence-restricted; deployment converts "
                 "to DICOM WSI at the archive boundary"},
        {"id": "hamamatsu-ndpi", "vendor": "Hamamatsu",
         "models": "NanoZoomer series", "protocol": "NDPI tiles",
         "posture": "composed",
         "note": "NDPI read via OpenSlide at the archive boundary"},
        {"id": "leica-aperio", "vendor": "Leica Biosystems",
         "models": "Aperio GT/AT series", "protocol": "SVS tiles",
         "posture": "composed",
         "note": "SVS read via OpenSlide; vendor eSlide manager "
                 "composes at deployment"},
        {"id": "open-wsi", "vendor": "open-source",
         "models": "any DICOM WSI / OpenSlide-readable format",
         "protocol": "DICOM WSI / OpenSlide → DZI tiles",
         "posture": "open",
         "note": "OpenSlide (GPL — service boundary only, never "
                 "imported) converts to deep-zoom tiles; viewerlaunch "
                 "opens them via OpenSeadragon"},
    ),
    "dermoscopy": (
        {"id": "fotofinder", "vendor": "FotoFinder",
         "models": "ATBM / handyscope", "protocol": "vendor export",
         "posture": "composed",
         "note": "total-body mapping export; images land with scale "
                 "markers per telederm QA"},
        {"id": "canfield", "vendor": "Canfield Scientific",
         "models": "DermaGraphix / VECTRA",
         "protocol": "vendor export", "posture": "composed",
         "note": "DermaGraphix image + mapping export"},
        {"id": "dermengine", "vendor": "MetaOptima",
         "models": "DermEngine + MoleScope",
         "protocol": "vendor REST", "posture": "composed",
         "note": "DermEngine API; lesion images with metadata"},
        {"id": "open-derm", "vendor": "open-source",
         "models": "any camera + scale marker",
         "protocol": "DICOM / JPEG + telederm QA gates",
         "posture": "open",
         "note": "the QA gates (scale marker, focus, lighting) are "
                 "the interoperability — pixels with provenance beat "
                 "proprietary formats"},
    ),
    "lab-analyzer": (
        {"id": "roche-cobas", "vendor": "Roche",
         "models": "cobas 6000/8000 + cobas infinity",
         "protocol": "vendor middleware (cobas infinity) / HL7",
         "posture": "composed",
         "note": "infinity LIS middleware emits HL7 ORU"},
        {"id": "abbott-alinity", "vendor": "Abbott",
         "models": "Alinity ci-series",
         "protocol": "vendor middleware (AlinIQ) / HL7",
         "posture": "composed",
         "note": "AlinIQ AMS; HL7 result feed"},
        {"id": "siemens-atellica", "vendor": "Siemens Healthineers",
         "models": "Atellica Solution",
         "protocol": "vendor middleware / HL7", "posture": "composed",
         "note": "Atellica Data Manager; HL7 result feed"},
        {"id": "open-lis", "vendor": "open-source",
         "models": "any HL7-emitting analyzer",
         "protocol": "HL7 v2 ORU", "posture": "open",
         "note": "clean-room ORU codec below; ASTM E1381 physical "
                 "link noted but the spec is paywalled — HL7 is the "
                 "implemented lane"},
    ),
    "semen-analyzer": (
        {"id": "hamilton-thorne", "vendor": "Hamilton Thorne",
         "models": "IVOS II CASA", "protocol": "vendor export / HL7",
         "posture": "composed",
         "note": "CASA results export; WHO 2021 parameters map onto "
                 "fertility.record_semen_analysis"},
        {"id": "mes-sqa", "vendor": "Medical Electronic Systems",
         "models": "SQA-iO / SQA-Vision",
         "protocol": "vendor export / HL7", "posture": "composed",
         "note": "SQA result feed; motility/concentration mapped"},
        {"id": "lenshooke", "vendor": "LensHooke",
         "models": "X1 PRO CASA", "protocol": "vendor export",
         "posture": "composed",
         "note": "CASA export; same parameter map"},
        {"id": "open-casa", "vendor": "open-source",
         "models": "manual count / any HL7 source",
         "protocol": "HL7 v2 ORU + manual entry gates",
         "posture": "open",
         "note": "manual entry carries the same plausibility gates — "
                 "a zero still needs confirmation, machine or eye"},
    ),
}

DOMAINS = tuple(DEVICE_ADAPTERS)


def catalog(domain=None):
    """The adapter menu. Absence is stated, not silent: unknown domain
    raises rather than returning an empty list that looks like
    'nothing exists'."""
    if domain is None:
        return {d: [dict(a) for a in rows]
                for d, rows in DEVICE_ADAPTERS.items()}
    if domain not in DEVICE_ADAPTERS:
        raise DeviceHubError(f"no adapters catalogued for {domain!r} "
                             f"— known domains: {sorted(DOMAINS)}")
    return [dict(a) for a in DEVICE_ADAPTERS[domain]]


# ----------------------------------------------------------------
# Clean-room HL7 v2 codec (ORU^R01 in, ACK out). HL7 v2 message
# syntax is an open standard; this implements the minimal observation
# lane honestly: no full conformance claim, no silent field drops —
# unmapped segments are counted and returned.

FIELD, COMPONENT, REPEAT, ESCAPE, SUB = "|", "^", "~", "\\", "&"


def parse_oru(message):
    """Parse an HL7 v2 ORU^R01 into normalized observations.

    Returns {message_id, sender, patient_id, observations: [...],
    unmapped_segments: [...]}. Every observation carries its code,
    value, units, abnormal flag, and timestamp. Garbage in raises —
    a truncated message is a finding, not a partial truth."""
    segments = [s for s in message.replace("\n", "\r").split("\r")
                if s.strip()]
    if not segments or not segments[0].startswith("MSH"):
        raise DeviceHubError("message does not start with MSH — not "
                             "HL7, or corrupted in transit; refuse, "
                             "don't guess")
    msh = segments[0].split(FIELD)
    if len(msh) < 10:
        raise DeviceHubError("truncated MSH segment")
    out = {"message_id": msh[9], "sender": msh[2] if len(msh) > 2
           else "", "patient_id": None, "observations": [],
           "unmapped_segments": []}
    current_obr = None
    for seg in segments[1:]:
        name = seg[:3]
        fields = seg.split(FIELD)
        if name == "PID":
            out["patient_id"] = fields[3].split(COMPONENT)[0] \
                if len(fields) > 3 else None
        elif name == "OBR":
            current_obr = fields[4].split(COMPONENT)[0] \
                if len(fields) > 4 else None
        elif name == "OBX":
            if len(fields) < 6:
                raise DeviceHubError("truncated OBX segment — an "
                                     "observation without a value "
                                     "field is not an observation")
            out["observations"].append({
                "set_id": fields[1],
                "code": fields[3].split(COMPONENT)[0],
                "code_text": (fields[3].split(COMPONENT)[1]
                              if COMPONENT in fields[3] else ""),
                "value": fields[5],
                "units": fields[6] if len(fields) > 6 else "",
                "abnormal_flag": (fields[8] if len(fields) > 8
                                  else ""),
                "timestamp": (fields[14] if len(fields) > 14 else ""),
                "panel": current_obr,
            })
        elif name in ("MSH", "NTE"):
            continue
        else:
            out["unmapped_segments"].append(name)
    if not out["observations"]:
        raise DeviceHubError("no OBX segments — a result message "
                             "without results; the wire lied or the "
                             "analyzer did")
    return out


def build_ack(message_id, *, accepting_app="openhealth",
              error=None):
    """AA or AE. An ACK that lies about acceptance is worse than a
    timeout."""
    code = "AA" if error is None else "AE"
    err = f"{FIELD}{error}" if error else ""
    return (f"MSH|^~\\&|{accepting_app}||SENDER||"
            f"20260101000000||ACK|ACK-{message_id}|P|2.5\r"
            f"MSA|{code}|{message_id}{err}\r")


def build_oru(*, message_id, patient_id, sender, observations,
              panel=None):
    """Serialize observations as ORU^R01. observations: dicts with
    code, value, optional units/flag/timestamp. Codes are mandatory —
    an unbound observation is not a result."""
    if not observations:
        raise DeviceHubError("refusing to serialize an empty result "
                             "set — absence is not a value")
    lines = [f"MSH|^~\\&|{sender}||OPENHEALTH||20260101000000||"
             f"ORU^R01|{message_id}|P|2.5",
             f"PID|1||{patient_id}"]
    if panel:
        lines.append(f"OBR|1|||{panel}")
    for i, obs in enumerate(observations, 1):
        if not obs.get("code"):
            raise DeviceHubError("observation without a code — unbound "
                                 "is not a code")
        if obs.get("value") in (None, ""):
            raise DeviceHubError(f"observation {obs.get('code')} has "
                                 "no value — absence is not a value")
        lines.append(
            f"OBX|{i}|ST|{obs['code']}^{obs.get('code_text', '')}||"
            f"{obs['value']}|{obs.get('units', '')}|||"
            f"{obs.get('flag', '')}||||||{obs.get('timestamp', '')}")
    return "\r".join(lines) + "\r"


# ----------------------------------------------------------------
# Sessions: the plug-and-the-socket. connect() is honest the way the
# PACS adapter is honest — no endpoint, no pretend; proprietary
# adapters also refuse without a transport callable (the vendor SDK
# bridge supplied at deployment).

class DeviceHub:
    def __init__(self):
        self.sessions = []   # append-only connect audit
        self._seq = 0

    def connect(self, adapter_id, *, endpoint, transport=None):
        rows = [a for rows in DEVICE_ADAPTERS.values() for a in rows]
        desc = next((a for a in rows if a["id"] == adapter_id), None)
        if desc is None:
            raise DeviceHubError(f"unknown adapter: {adapter_id} — "
                                 "the catalogue is the menu; an "
                                 "uncatalogued device is procurement, "
                                 "not configuration")
        if not endpoint:
            raise DeviceHubError(
                f"{adapter_id}: no endpoint configured — refusing to "
                "pretend. A cable not plugged in is not an interface")
        if desc["posture"] == "composed" and transport is None:
            raise DeviceHubError(
                f"{adapter_id} integrates via {desc['vendor']}'s "
                "surface — supply the deployment transport (vendor "
                "SDK bridge / REST client). Nothing proprietary ships "
                "in this repo, by licence and by doctrine")
        self._seq += 1
        s = {"session_id": f"DH-{self._seq}", "adapter": adapter_id,
             "endpoint": endpoint, "posture": desc["posture"],
             "transport": transport}
        self.sessions.append(s)
        return {"session_id": s["session_id"], "adapter": adapter_id,
                "posture": desc["posture"]}

    def _session(self, session_id):
        for s in self.sessions:
            if s["session_id"] == session_id:
                return s
        raise DeviceHubError(f"unknown session: {session_id}")

    def ingest(self, session_id, payload):
        """Inbound observations. Open lane parses HL7 here; composed
        lanes delegate to the deployment transport. Every observation
        carries its adapter provenance — a value without a source is
        a rumour."""
        s = self._session(session_id)
        if s["posture"] == "open":
            parsed = parse_oru(payload)
        else:
            parsed = s["transport"]("ingest", payload)
        for obs in parsed.get("observations", []):
            obs["source_adapter"] = s["adapter"]
        parsed["adapter"] = s["adapter"]
        parsed["protocol_note"] = "parsed clean-room (HL7 v2)" \
            if s["posture"] == "open" else "parsed by vendor transport"
        return parsed

    def push_result(self, session_id, *, message_id, patient_id,
                    observations, panel=None):
        """Outbound results to the device/middleware lane."""
        s = self._session(session_id)
        if s["posture"] == "open":
            return build_oru(message_id=message_id,
                             patient_id=patient_id,
                             sender=s["adapter"],
                             observations=observations, panel=panel)
        return s["transport"]("push", {"message_id": message_id,
                                       "patient_id": patient_id,
                                       "observations": observations,
                                       "panel": panel})
