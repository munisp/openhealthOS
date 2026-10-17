/* Patient app i18n — the same five product languages, in the voice of a
 * patient rather than a clinician: plain, warm, short. */

export const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "pcm", name: "Naija (Pidgin)" },
  { code: "ha", name: "Hausa" },
  { code: "yo", name: "Yorùbá" },
  { code: "ig", name: "Igbo" },
];

const STRINGS = {
  en: {
    app_name: "My Health", welcome: "Welcome", sign_in: "View my health updates",
    patient_code: "Your patient code", phone: "Phone number you registered with",
    code_hint: "The clinic gave you this code on your card or by SMS.",
    sign_in_btn: "View updates", wrong_code: "Code or phone number not recognised — check with your clinic.",
    updates: "Your updates", action_needed: "Needs your action", earlier: "Earlier",
    no_updates: "No updates yet — we will message you here and by SMS.",
    kind_appointment: "Appointment", kind_lab: "Test result ready", kind_pickup: "Medication pickup",
    kind_referral: "Referral", kind_immunization: "Immunisation reminder", kind_general: "Update",
    offline: "Offline — showing your last updates", last_updated: "Last updated", refresh: "Refresh",
    sign_out: "Sign out", language: "Language",
    state_sent: "delivered by SMS", state_pending: "on its way", state_failed: "SMS could not send — show this screen at the clinic",
    loading: "Checking…",
  },
  pcm: {
    app_name: "My Health", welcome: "Welcome", sign_in: "See my health matter",
    patient_code: "Your patient code", phone: "The phone number wey you register",
    code_hint: "Na clinic give you this code for card or SMS.",
    sign_in_btn: "See updates", wrong_code: "We no find this code or number — ask your clinic.",
    updates: "Your updates", action_needed: "Wetin you suppose do", earlier: "Before before",
    no_updates: "No update yet — we go message you for here and SMS.",
    kind_appointment: "Appointment", kind_lab: "Test result don ready", kind_pickup: "Your medicine dey ready",
    kind_referral: "Referral", kind_immunization: "Immunization reminder", kind_general: "Update",
    offline: "No network — na your last updates you dey see", last_updated: "Last updated", refresh: "Refresh",
    sign_out: "Comot", language: "Language",
    state_sent: "dem don send SMS", state_pending: "e dey come", state_failed: "SMS no send — show this screen for clinic",
    loading: "We dey check…",
  },
  ha: {
    app_name: "Lafiyata", welcome: "Barka da zuwa", sign_in: "Duba sabuntawar lafiyata",
    patient_code: "Lambar majinyaci", phone: "Lambar wayar da ka yi rajista da ita",
    code_hint: "Asibiti ya ba ka wannan lamba a kati ko ta SMS.",
    sign_in_btn: "Duba sabuntawa", wrong_code: "Ba mu gane lambar ko wayar ba — tuntubi asibiti.",
    updates: "Sabuntawar ka", action_needed: "Abin da ya kamata ka yi", earlier: "Na baya",
    no_updates: "Babu sabuntawa tukuna — za mu aiko maka da saƙo nan da ta SMS.",
    kind_appointment: "Alƙawari", kind_lab: "Sakamakon gwaji ya fito", kind_pickup: "Magani ya shirya",
    kind_referral: "Canjin asibiti", kind_immunization: "Tunar da rigakafi", kind_general: "Sabuntawa",
    offline: "Babu network — ga sabuntawar ka na karshe", last_updated: "An sabunta karshe", refresh: "Sabunta",
    sign_out: "Fita", language: "Harshe",
    state_sent: "an aika SMS", state_pending: "yana kan hanya", state_failed: "SMS ya kasa — nuna wannan allo a asibiti",
    loading: "Ana dubawa…",
  },
  yo: {
    app_name: "Ìlera Mi", welcome: "Káàbọ̀", sign_in: "Wo àwọn ìsọfúnni ìlera mi",
    patient_code: "Kóòdù aláìsàn rẹ", phone: "Nọ́mbà fóònù tí o forúkọsilẹ̀ pẹ̀lú",
    code_hint: "Ilé ìwòsàn ni ó fún ọ ní kóòdù yìí lórí káàdì tàbí SMS.",
    sign_in_btn: "Wo àwọn ìsọfúnni", wrong_code: "A kò rí kóòdù tàbí nọ́mbà yìí — béèrè ní ilé ìwòsàn.",
    updates: "Àwọn ìsọfúnni rẹ", action_needed: "Ohun tí o yẹ kí o ṣe", earlier: "Tẹ́lẹ̀",
    no_updates: "Kò sí ìsọfúnni rí — a ó firánṣẹ́ níbí àti ní SMS.",
    kind_appointment: "Ìpàdé", kind_lab: "Èsì àyẹ̀wò ti sí", kind_pickup: "Oògùn rẹ ti ṣetan",
    kind_referral: "Ìránṣẹ́ sí ilé ìwòsàn mìíràn", kind_immunization: "Ìránilétí ajẹsára", kind_general: "Ìsọfúnni",
    offline: "Kò sí ayélujára — àwọn ìsọfúnni gbèyìn rẹ ni o Ń rí", last_updated: "Ìgbà gbèyìn tí a ṣàtúnṣe", refresh: "Ṣàtúnṣe",
    sign_out: "Jáde", language: "Èdè",
    state_sent: "ti rán SMS", state_pending: "ó Ń bọ̀", state_failed: "SMS kò lọ — fi ibojú yìí hàn ní ilé ìwòsàn",
    loading: "Ń ṣàyẹ̀wò…",
  },
  ig: {
    app_name: "Ahụ Ike M", welcome: "Nnọọ", sign_in: "Lelee ozi ahụ ike m",
    patient_code: "Koodu ọrịa gị", phone: "Nọmba ekwentị ị debanyere",
    code_hint: "Ụlọ ọgwụ nyere gị koodu a na kaadị ma ọ bụ SMS.",
    sign_in_btn: "Lelee ozi", wrong_code: "Anyị achọtaghị koodu ma ọ bụ nọmba a — jụọ na ụlọ ọgwụ.",
    updates: "Ozi gị", action_needed: "Ihe ị kwesịrị ime", earlier: "N'oge gara aga",
    no_updates: "Ozi ọ bụla adịghị — anyị ga-ezitere gị ebe a na SMS.",
    kind_appointment: "Nhazi", kind_lab: "Nsonaazụ nyocha adịla njikere", kind_pickup: "Ọgwụ gị adịla njikere",
    kind_referral: "Ntụnye", kind_immunization: "Ncheta mgbochi ọrịa", kind_general: "Ozi",
    offline: "Enweghị netwọk — ozi ikpeazụ gị ka ị na-ahụ", last_updated: "Emelitere ikpeazụ", refresh: "Melite",
    sign_out: "Pụọ", language: "Asụsụ",
    state_sent: "ezigala SMS", state_pending: "ọ na-abịa", state_failed: "SMS agaghị — gosi ihuenyo a na ụlọ ọgwụ",
    loading: "Na-enyocha…",
  },
};

let current = "en";

export function setLanguage(code) {
  current = STRINGS[code] ? code : "en";
}
export function getLanguage() {
  return current;
}
export function t(key) {
  return STRINGS[current]?.[key] ?? STRINGS.en[key] ?? key;
}
