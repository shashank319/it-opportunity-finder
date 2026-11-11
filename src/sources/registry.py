"""
registry.py — turns the `sources:` section of config.yaml into live adapters.

This is the ONE place that knows which adapter classes exist. To add a whole
new KIND of source later (e.g. an RSS adapter or a state scraper), you:
  1. write the adapter class (subclass of Source),
  2. add one line to build_sources() below.
Adding another instance of an EXISTING kind (e.g. another Socrata dataset)
needs no code — just a new config entry.
"""

from __future__ import annotations

from .socrata import SocrataSource
from .gmail_alerts import GmailAlertsSource
from .samgov import SamGovSource
from .planetbids import PlanetBidsSource
from .opengov import OpenGovSource
from .arcgis import ArcGisSource
from .bidnetdirect import BidNetDirectSource
from .biddingo import BiddingoSource
from .bonfire import BonfireSource
from .bidsandtenders import BidsAndTendersSource
from .ionwave import IonwaveSource
from .buyspeed import BuySpeedSource
from .ivalua import IvaluaSource
from .oracle_fusion import OracleFusionSource
from .infor_sourcing import InforSourcingSource
from .jaggaer_public import JaggaerPublicSource
from .procureware import ProcureWareSource
from .beaconbid import BeaconBidSource
from .vendorlink import VendorLinkSource
from .bid_page import BidPageSource
from .nc_evp import NCEVPSource
from .georgia_gpr import GeorgiaGPRSource
from .pa_emarketplace import PAEMarketplaceSource
from .florida_vbs import FloridaVBSSource
from .cal_eprocure import CalEProcureSource
from .iowa_bids import IowaBidsSource
from .austin_tx import AustinTXSource
from .il_higher_ed import ILHigherEdSource
from .camden_county_nj import CamdenCountyNJSource
from .questcdn import QuestCdnSource
from .rtvision import RtVisionSource
from .civicplus_bids import CivicPlusBidsSource
from .schenectady_county import SchenectadyCountySource
from .la_county import LACountySource
from .tennessee_cpo import TennesseeCPOSource
from .atlanta_regional import AtlantaRegionalSource
from .tyler_munis import TylerMunisSource
from .nypa import NypaSource
from .demandstar import DemandStarSource
from .nyscr import NyscrSource
from .wa_webs import WaWebsSource
from .bidlocker import BidLockerSource
from .peoplesoft_bids import PeopleSoftBidsSource


def build_sources(config: dict) -> list:
    """Read config['sources'] and return a list of enabled Source instances."""
    sources = []
    sources_cfg = config.get("sources", {})

    # --- Socrata open-data datasets (zero or more) ---
    for ds in sources_cfg.get("socrata_datasets", []):
        if ds.get("enabled", True):
            sources.append(SocrataSource(ds))

    # --- Gmail bid-alert ingestion (at most one) ---
    gmail_cfg = sources_cfg.get("gmail_alerts", {})
    if gmail_cfg.get("enabled", False):
        sources.append(GmailAlertsSource(gmail_cfg))

    # --- SAM.gov federal opportunities (the one federal source) ---
    samgov_cfg = sources_cfg.get("samgov", {})
    if samgov_cfg.get("enabled", False):
        sources.append(SamGovSource(samgov_cfg))

    # --- PlanetBids agency portals (one adapter, many agencies) ---
    planetbids_cfg = sources_cfg.get("planetbids", {})
    if planetbids_cfg.get("enabled", False):
        sources.append(PlanetBidsSource(planetbids_cfg))

    # --- OpenGov Procurement portals (one adapter, many agencies) ---
    opengov_cfg = sources_cfg.get("opengov", {})
    if opengov_cfg.get("enabled", False):
        sources.append(OpenGovSource(opengov_cfg))

    # --- ArcGIS feature layers (zero or more) ---
    for layer in sources_cfg.get("arcgis_layers", []):
        if layer.get("enabled", True):
            sources.append(ArcGisSource(layer))

    # --- BidNet Direct agency portals (one adapter, many agencies) ---
    bidnetdirect_cfg = sources_cfg.get("bidnetdirect", {})
    if bidnetdirect_cfg.get("enabled", False):
        sources.append(BidNetDirectSource(bidnetdirect_cfg))

    # --- Biddingo agency portals (one adapter, many agencies) ---
    biddingo_cfg = sources_cfg.get("biddingo", {})
    if biddingo_cfg.get("enabled", False):
        sources.append(BiddingoSource(biddingo_cfg))

    # --- Bonfire agency portals (one adapter, many agencies) ---
    bonfire_cfg = sources_cfg.get("bonfire", {})
    if bonfire_cfg.get("enabled", False):
        sources.append(BonfireSource(bonfire_cfg))

    # --- Bids&Tenders agency portals (needs Playwright; one adapter, many agencies) ---
    bidsandtenders_cfg = sources_cfg.get("bidsandtenders", {})
    if bidsandtenders_cfg.get("enabled", False):
        sources.append(BidsAndTendersSource(bidsandtenders_cfg))

    # --- Ionwave agency portals (one adapter, many agencies) ---
    ionwave_cfg = sources_cfg.get("ionwave", {})
    if ionwave_cfg.get("enabled", False):
        sources.append(IonwaveSource(ionwave_cfg))

    # --- BuySpeed (Periscope) portals (one adapter, many portals) ---
    buyspeed_cfg = sources_cfg.get("buyspeed", {})
    if buyspeed_cfg.get("enabled", False):
        sources.append(BuySpeedSource(buyspeed_cfg))

    # --- Ivalua portals (one adapter, many tenants) ---
    ivalua_cfg = sources_cfg.get("ivalua", {})
    if ivalua_cfg.get("enabled", False):
        sources.append(IvaluaSource(ivalua_cfg))

    # --- Oracle Fusion Cloud Procurement (one adapter, many agencies) ---
    oracle_fusion_cfg = sources_cfg.get("oracle_fusion", {})
    if oracle_fusion_cfg.get("enabled", False):
        sources.append(OracleFusionSource(oracle_fusion_cfg))

    # --- Infor CloudSuite supplier portals (one adapter, many agencies) ---
    infor_cfg = sources_cfg.get("infor_sourcing", {})
    if infor_cfg.get("enabled", False):
        sources.append(InforSourcingSource(infor_cfg))

    # --- JAGGAER / SciQuest public event lists (one adapter, many orgs) ---
    jaggaer_cfg = sources_cfg.get("jaggaer_public", {})
    if jaggaer_cfg.get("enabled", False):
        sources.append(JaggaerPublicSource(jaggaer_cfg))

    # --- ProcureWare portals (one adapter, many tenants) ---
    procureware_cfg = sources_cfg.get("procureware", {})
    if procureware_cfg.get("enabled", False):
        sources.append(ProcureWareSource(procureware_cfg))

    # --- BeaconBid agencies (one adapter, many agencies) ---
    beaconbid_cfg = sources_cfg.get("beaconbid", {})
    if beaconbid_cfg.get("enabled", False):
        sources.append(BeaconBidSource(beaconbid_cfg))

    # --- VendorLink agencies (one adapter, many agencies) ---
    vendorlink_cfg = sources_cfg.get("vendorlink", {})
    if vendorlink_cfg.get("enabled", False):
        sources.append(VendorLinkSource(vendorlink_cfg))

    # --- QuestCDN agency portals (one adapter, many agencies) ---
    questcdn_cfg = sources_cfg.get("questcdn", {})
    if questcdn_cfg.get("enabled", False):
        sources.append(QuestCdnSource(questcdn_cfg))

    # --- RTVision Connex (one shared feed, filtered to configured agencies) ---
    rtvision_cfg = sources_cfg.get("rtvision", {})
    if rtvision_cfg.get("enabled", False):
        sources.append(RtVisionSource(rtvision_cfg))

    # --- CivicPlus Bids.aspx municipal sites (one adapter, many agencies) ---
    civicplus_cfg = sources_cfg.get("civicplus_bids", {})
    if civicplus_cfg.get("enabled", False):
        sources.append(CivicPlusBidsSource(civicplus_cfg))

    # --- Schenectady County NY (one-off Drupal Views listing) ---
    schenectady_cfg = sources_cfg.get("schenectady_county", {})
    if schenectady_cfg.get("enabled", False):
        sources.append(SchenectadyCountySource(schenectady_cfg))

    # --- Los Angeles County CA (one-off LACoBids list) ---
    la_county_cfg = sources_cfg.get("la_county", {})
    if la_county_cfg.get("enabled", False):
        sources.append(LACountySource(la_county_cfg))

    # --- Tennessee Central Procurement Office (one-off tn.gov tables) ---
    tennessee_cfg = sources_cfg.get("tennessee_cpo", {})
    if tennessee_cfg.get("enabled", False):
        sources.append(TennesseeCPOSource(tennessee_cfg))

    # --- Atlanta Regional Commission GA (one-off WordPress API) ---
    arc_cfg = sources_cfg.get("atlanta_regional", {})
    if arc_cfg.get("enabled", False):
        sources.append(AtlantaRegionalSource(arc_cfg))

    # --- Tyler Munis Self Service portals ---
    tyler_munis_cfg = sources_cfg.get("tyler_munis", {})
    if tyler_munis_cfg.get("enabled", False):
        sources.append(TylerMunisSource(tyler_munis_cfg))

    # --- NY Power Authority (one-off JSON API) ---
    nypa_cfg = sources_cfg.get("nypa", {})
    if nypa_cfg.get("enabled", False):
        sources.append(NypaSource(nypa_cfg))

    # --- DemandStar agency pages ---
    demandstar_cfg = sources_cfg.get("demandstar", {})
    if demandstar_cfg.get("enabled", False):
        sources.append(DemandStarSource(demandstar_cfg))

    # --- NYS Contract Reporter (one-off ad search) ---
    nyscr_cfg = sources_cfg.get("nyscr", {})
    if nyscr_cfg.get("enabled", False):
        sources.append(NyscrSource(nyscr_cfg))

    # --- Washington WEBS (one-off bid calendar) ---
    wa_webs_cfg = sources_cfg.get("wa_webs", {})
    if wa_webs_cfg.get("enabled", False):
        sources.append(WaWebsSource(wa_webs_cfg))

    # --- Bid Locker agency RSS feeds ---
    bidlocker_cfg = sources_cfg.get("bidlocker", {})
    if bidlocker_cfg.get("enabled", False):
        sources.append(BidLockerSource(bidlocker_cfg))

    # --- PeopleSoft public bidding pages ---
    peoplesoft_bids_cfg = sources_cfg.get("peoplesoft_bids", {})
    if peoplesoft_bids_cfg.get("enabled", False):
        sources.append(PeopleSoftBidsSource(peoplesoft_bids_cfg))

    # --- Custom city/county bid pages (generic heuristic parser) ---
    bid_page_cfg = sources_cfg.get("bid_page", {})
    if bid_page_cfg.get("enabled", False):
        sources.append(BidPageSource(bid_page_cfg))

    # --- North Carolina eVP (one-off) ---
    nc_evp_cfg = sources_cfg.get("nc_evp", {})
    if nc_evp_cfg.get("enabled", False):
        sources.append(NCEVPSource(nc_evp_cfg))

    # --- Georgia Procurement Registry (one-off) ---
    georgia_gpr_cfg = sources_cfg.get("georgia_gpr", {})
    if georgia_gpr_cfg.get("enabled", False):
        sources.append(GeorgiaGPRSource(georgia_gpr_cfg))

    # --- Pennsylvania eMarketplace (one-off) ---
    pa_emarketplace_cfg = sources_cfg.get("pa_emarketplace", {})
    if pa_emarketplace_cfg.get("enabled", False):
        sources.append(PAEMarketplaceSource(pa_emarketplace_cfg))

    # --- Florida Vendor Bid System (one-off) ---
    florida_vbs_cfg = sources_cfg.get("florida_vbs", {})
    if florida_vbs_cfg.get("enabled", False):
        sources.append(FloridaVBSSource(florida_vbs_cfg))

    # --- California Cal eProcure (one-off) ---
    cal_eprocure_cfg = sources_cfg.get("cal_eprocure", {})
    if cal_eprocure_cfg.get("enabled", False):
        sources.append(CalEProcureSource(cal_eprocure_cfg))

    # --- State of Iowa — Bid Opportunities (one-off) ---
    iowa_bids_cfg = sources_cfg.get("iowa_bids", {})
    if iowa_bids_cfg.get("enabled", False):
        sources.append(IowaBidsSource(iowa_bids_cfg))

    # --- City of Austin, TX (one-off) ---
    austin_tx_cfg = sources_cfg.get("austin_tx", {})
    if austin_tx_cfg.get("enabled", False):
        sources.append(AustinTXSource(austin_tx_cfg))

    # --- Illinois Higher Ed Procurement Bulletin (one-off) ---
    il_higher_ed_cfg = sources_cfg.get("il_higher_ed", {})
    if il_higher_ed_cfg.get("enabled", False):
        sources.append(ILHigherEdSource(il_higher_ed_cfg))

    # --- Camden County, NJ (one-off) ---
    camden_county_nj_cfg = sources_cfg.get("camden_county_nj", {})
    if camden_county_nj_cfg.get("enabled", False):
        sources.append(CamdenCountyNJSource(camden_county_nj_cfg))

    return sources
