"""vhdemo — presenter walkthrough generator for a VHC scheme demo
(vhc-demo@1.0.0).

A ministry demo is a claim about a system. This module makes the claim
auditable: the walkthrough script is *generated from the seeded pilot
narrative*, scene by scene, and every number on a slide or in a talking
point comes out of the narrative dict — never invented by the presenter,
never hardcoded by us. Change the seed, the script changes; the doctrine
does not.

Doctrine:
  - the script reads the narrative, it does not improvise
  - every scene names the UI surface the presenter is standing on
  - every scene names the failure mode it is answering ("what stops X?")
  - denominators travel with every number
  - synthetic means synthetic: the script says so on scene one
"""

from __future__ import annotations

DEMO_VERSION = "vhc-demo@1.0.0"

# The scene order is the story arc: a traveller arrives, money moves,
# care happens, the system watches itself, and the ministry can prove it.
# Each entry: (scene_id, surface, the question the audience is silently
# asking, builder). Builders receive the narrative and return talking
# points — a list of strings. No builder may invent a number.

SCENES = ("arrival", "verdict", "money", "care", "rescue",
          "surveillance", "oversight", "proof")

# Declared rehearsal pace — seconds per scene. A halt condition invented
# mid-incident is an excuse; a pace invented mid-demo is rambling. The
# clock is declared here, versioned with the script, and the rehearsal
# mode measures against it.
SCENE_TIMING = {"arrival": 120, "verdict": 120, "money": 180, "care": 150,
                "rescue": 120, "surveillance": 150, "oversight": 120,
                "proof": 90}

def _fmt_minor(minor: int, symbol: str) -> str:
    if symbol == "CVE":
        return _fmt_cve(minor)
    return f"{symbol}{minor / 100:,.2f}"


def _fmt_cve(eur_minor: int) -> str:
    """Escudo display off the BCV peg in the cv-vhc profile — the rate is
    read from the versioned profile, never hardcoded here. Escudo style:
    thousands separated with '.', '$' before the two decimals."""
    from vhcover import VHC_PROFILES
    code, peg = VHC_PROFILES["cv-vhc@1.0.0"]["alt_currency"]
    assert code == "CVE"
    cve = eur_minor / 100 * peg
    escudos = int(cve)
    centavos = round((cve - escudos) * 100)
    if centavos == 100:                       # carry on rounding
        escudos, centavos = escudos + 1, 0
    return f"{escudos:,}${centavos:02d}".replace(",", ".")


def _scene_arrival(n, currency_symbol):
    return [
        ("arrival.volume",
         f"DAY ONE, LANE THREE. {n['arrivals']} travellers arrive over "
         f"{n['days']} days — every one synthetic (Pax-NNNN, SIM-NNNN; "
         "this demo maps to no person, and the registry can prove it).",
         {"arrivals": n["arrivals"], "days": n["days"]}),
        ("arrival.covers",
         f"Each buys or carries cover: {n['covers']} covers issued, of "
         f"which {n['exempt']} are lawful exemptions — a diplomat and a "
         "transit passenger are not revenue, and the system never treats "
         "them as such.",
         {"covers": n["covers"], "exempt": n["exempt"]}),
        ("arrival.qr",
         "Point at the QR on the officer's screen: compact JWS, signed by "
         "the HSM-held key, verifiable offline for 72 hours. A stale "
         "cache shows RED, not a silent pass.",
         {}),
    ]


def _scene_verdict(n, currency_symbol):
    v = n["verdicts"]
    return [
        ("verdict.decision",
         "THE VERDICT IS A DECISION, NOT A VIBE. Every scan returns "
         "green, amber, or red with a *named reason* and a named officer "
         "action — admit, desk, or supervisor.",
         {}),
        ("verdict.fortnight",
         f"In this fortnight: {v.get('green', 0)} green, "
         f"{v.get('amber', 0)} amber (desk lookup, resolve on the spot), "
         f"{v.get('red', 0)} red. Denominators on screen, always.",
         {"green": v.get("green", 0), "amber": v.get("amber", 0),
          "red": v.get("red", 0)}),
        ("verdict.privacy",
         "The officer never sees a raw passport number — the desk lookup "
         "works from the cover reference.",
         {}),
    ]


def _scene_money(n, currency_symbol):
    sym = currency_symbol
    premium = _fmt_minor(n["premium_collected_minor"], sym)
    return [
        ("money.five",
         "FOLLOW THE MONEY — ALL FIVE MONIES. Premium collected: "
         f"{premium}. It splits at "
         "the moment of sale into reserve, claims, ops, surveillance, and "
         "administration buckets per the versioned country profile — a "
         "rule that cannot be traced to a table is a bribe with a receipt.",
         {"premium": premium}),
        ("money.channel",
         f"Channel mix: {n['partner_sales']} sales came through the "
         f"partner API, {n['groups']} group manifests (airline, tour "
         "operator) — all-or-nothing, priced per head.",
         {"partner_sales": n["partner_sales"], "groups": n["groups"]}),
        ("money.refunds",
         f"Duplicate charges refund themselves: {n['auto_refunds']} "
         "auto-refunds this fortnight, first charge kept, the rest "
         "returned inside the window.",
         {"auto_refunds": n["auto_refunds"]}),
        ("money.settlement",
         f"Settlement reconciles daily: {n['settlement_breaches']} "
         "breaches against the 1000 ppm break tolerance. Zero is the "
         "expected answer, and the report says the denominator.",
         {"settlement_breaches": n["settlement_breaches"]}),
    ]


def _scene_care(n, currency_symbol):
    paid = _fmt_minor(n["claims_paid_minor"], currency_symbol)
    loss = f"{n['loss_ratio']:.4f}"
    return [
        ("care.sick",
         "WHEN SOMEONE GETS SICK. A traveller walks into an accredited "
         "clinic; the provider portal sees only its own cases.",
         {}),
        ("care.clock",
         f"{n['claims']} claims this fortnight. The clock that matters: "
         "GOP inside 2 hours of intake — the guarantee of payment is what "
         "gets the traveller treated without a credit card on a gurney.",
         {"claims": n["claims"]}),
        ("care.adjudicated",
         f"Adjudicated against the four-eyes tariff book; paid "
         f"{paid}. Loss "
         f"ratio {loss} — the actuarial mart reads the "
         "same registries, so finance and operations never argue about "
         "whose number is real.",
         {"paid": paid, "loss_ratio": loss}),
    ]


def _scene_rescue(n, currency_symbol):
    return [
        ("rescue.worst",
         "THE WORST DAY. A medevac request: approve, dispatch inside the "
         f"2-hour clock, arrive, close. This fortnight: {n['medevacs']} "
         "medevac(s).",
         {"medevacs": n["medevacs"]}),
        ("rescue.uninsured",
         "If an *uninsured* traveller needs rescue, the scheme does not "
         "let them die at the border of a spreadsheet: the uninsured fund "
         "posts the cost, and subrogation pursues recovery afterwards — "
         "bounded by what is actually outstanding, never a blank cheque.",
         {}),
    ]


def _scene_surveillance(n, currency_symbol):
    ack = "yes" if n["br10_all_acknowledged"] else "NO — STOP THE DEMO"
    return [
        ("surveillance.watches",
         "THE SYSTEM WATCHES ITSELF. Every incident is pseudonymized at "
         "registration — salted hash, no reversible identity in the "
         "registry. Synthetic here, protected in production.",
         {}),
        ("surveillance.br10",
         f"BR-10: five GI cases in one facility in one week trips an "
         f"alert. This fortnight: {n['br10_alerts']} alert(s), "
         f"acknowledged: {ack}. "
         "An unacknowledged alert is a finding, not a statistic.",
         {"br10_alerts": n["br10_alerts"], "ack": ack}),
        ("surveillance.export",
         "The IHR/UKHSA/ECDC export pack carries a SHA-256 — the "
         "checksum is the contract — and k-floor 5 suppression means "
         "small cells never leak a person. Pack hash: "
         f"{n['export_pack_sha256'][:16]}…",
         {"pack_hash": n["export_pack_sha256"][:16]}),
    ]


def _scene_oversight(n, currency_symbol):
    sla = n["helpdesk_sla"]
    return [
        ("oversight.phone",
         "WHO ANSWERS THE PHONE. Travellers get a 24/7 helpdesk with "
         "content in their language; a missing translation falls back "
         "*with the fallback stated* — nobody silently signs a waiver "
         "they cannot read.",
         {}),
        ("oversight.sla",
         f"{n['tickets']} tickets this fortnight; first-response SLA "
         f"compliance {sla}. A late response is a named breach with an "
         "owner, not a shrug.",
         {"tickets": n["tickets"], "sla": sla}),
        ("oversight.clocks",
         "Every ops clock — GOP 2h, adjudication 10d, payment 30d, "
         "appeal 5d, dispatch 2h, recon daily — has a named owner and a "
         "visible denominator. The dashboards read the reports, not vibes.",
         {}),
    ]


def _scene_proof(n, currency_symbol):
    return [
        ("proof.believe",
         "WHY YOU CAN BELIEVE ANY OF THIS. This entire fortnight is one "
         f"function call: seed_pilot(seed={n['seed']}). Same seed, same "
         "pilot, byte for byte — a demo you cannot reproduce is a story "
         "you cannot audit.",
         {"seed": n["seed"]}),
        ("proof.suites",
         "Every claim made on these slides is backed by a module with "
         "its own test suite; the platform runs its full suite on the "
         "deployment tag before a gate flips.",
         {}),
        ("proof.environment",
         "What is still environment, and we say so honestly: the live "
         "PSP contract, the HSM ceremony, and the lane kit are proven in "
         "the go-live gates G0–G5, not in this demo.",
         {}),
    ]


_BUILDERS = {
    "arrival": _scene_arrival, "verdict": _scene_verdict,
    "money": _scene_money, "care": _scene_care, "rescue": _scene_rescue,
    "surveillance": _scene_surveillance, "oversight": _scene_oversight,
    "proof": _scene_proof,
}

# --- presenter languages ------------------------------------------------
# The walkthrough speaks English and Portuguese (Cabo Verde). A point
# without a PT template is NOT silently shipped in English inside a
# Portuguese deck: it falls back *with the fallback stated* and named in
# the coverage report. Denominators always visible.

PT_TEMPLATES = {
    "arrival.volume":
        "DIA UM, CORREDOR TRÊS. {arrivals} viajantes chegam ao longo de "
        "{days} dias — todos sintéticos (Pax-NNNN, SIM-NNNN; esta "
        "demonstração não corresponde a nenhuma pessoa, e o registo "
        "consegue prová-lo).",
    "arrival.covers":
        "Cada um compra ou traz cobertura: {covers} coberturas emitidas, "
        "das quais {exempt} são isenções legais — um diplomata e um "
        "passageiro em trânsito não são receita, e o sistema nunca os "
        "trata como tal.",
    "arrival.qr":
        "Aponte para o QR no ecrã do funcionário: JWS compacto, assinado "
        "pela chave guardada no HSM, verificável offline durante 72 "
        "horas. Uma cache expirada mostra VERMELHO, não um passe "
        "silencioso.",
    "verdict.decision":
        "O VEREDITO É UMA DECISÃO, NÃO UMA INTUIÇÃO. Cada leitura devolve "
        "verde, âmbar ou vermelho com um *motivo nomeado* e uma ação "
        "nomeada do funcionário — admitir, balcão ou supervisor.",
    "verdict.fortnight":
        "Nesta quinzena: {green} verdes, {amber} âmbar (consulta ao "
        "balcão, resolvido no momento), {red} vermelhos. Denominadores "
        "no ecrã, sempre.",
    "verdict.privacy":
        "O funcionário nunca vê um número de passaporte em claro — a "
        "consulta ao balcão funciona a partir da referência da cobertura.",
    "money.five":
        "SIGA O DINHEIRO — AS CINCO VERBAS. Prémio cobrado: {premium}. "
        "Divide-se no momento da venda em dotações de reserva, sinistros, "
        "operações, vigilância e administração conforme o perfil de país "
        "versionado — uma regra que não pode ser rastreada até uma tabela "
        "é um suborno com recibo.",
    "money.channel":
        "Mistura de canais: {partner_sales} vendas pela API de "
        "parceiros, {groups} manifestos de grupo (companhia aérea, "
        "operador turístico) — tudo-ou-nada, com preço por pessoa.",
    "money.refunds":
        "Cobranças duplicadas reembolsam-se a si próprias: "
        "{auto_refunds} reembolsos automáticos nesta quinzena; a "
        "primeira cobrança fica, as restantes voltam dentro da janela.",
    "money.settlement":
        "A liquidação reconcilia diariamente: {settlement_breaches} "
        "quebras contra a tolerância de 1000 ppm. Zero é a resposta "
        "esperada, e o relatório mostra o denominador.",
    "care.sick":
        "QUANDO ALGUÉM ADOECE. Um viajante entra numa clínica "
        "credenciada; o portal do prestador vê apenas os seus próprios "
        "casos.",
    "care.clock":
        "{claims} sinistros nesta quinzena. O relógio que importa: GOP "
        "dentro de 2 horas após a admissão — a garantia de pagamento é "
        "o que faz o viajante ser tratado sem cartão de crédito numa "
        "maca.",
    "care.adjudicated":
        "Adjudicado contra a tabela de tarifas a quatro olhos; pago "
        "{paid}. Rácio de sinistralidade {loss_ratio} — o mart atuarial "
        "lê os mesmos registos, por isso finanças e operações nunca "
        "discutem sobre qual número é real.",
    "rescue.worst":
        "O PIOR DIA. Um pedido de evacuação médica: aprovar, despachar "
        "dentro do relógio de 2 horas, chegar, encerrar. Nesta "
        "quinzena: {medevacs} evacuação(ões).",
    "rescue.uninsured":
        "Se um viajante *sem seguro* precisar de resgate, o esquema não "
        "o deixa morrer na fronteira de uma folha de cálculo: o fundo "
        "de não segurados regista o custo e a sub-rogação procura a "
        "recuperação depois — limitada ao que está realmente em dívida, "
        "nunca um cheque em branco.",
    "surveillance.watches":
        "O SISTEMA VIGIA-SE A SI PRÓPRIO. Cada incidente é pseudonimizado "
        "no registo — hash com sal, sem identidade reversível no registo. "
        "Sintético aqui, protegido em produção.",
    "surveillance.br10":
        "BR-10: cinco casos GI numa unidade numa semana disparam um "
        "alerta. Nesta quinzena: {br10_alerts} alerta(s), reconhecido: "
        "{ack}. Um alerta não reconhecido é uma constatação, não uma "
        "estatística.",
    "surveillance.export":
        "O pacote de exportação IHR/UKHSA/ECDC traz um SHA-256 — o "
        "checksum é o contrato — e a supressão com limiar k=5 garante "
        "que células pequenas nunca revelam uma pessoa. Hash do pacote: "
        "{pack_hash}…",
    "oversight.phone":
        "QUEM ATENDE O TELEFONE. Os viajantes têm um balcão de apoio "
        "24/7 com conteúdo na sua língua; uma tradução em falta recua "
        "*com o recuo declarado* — ninguém assina silenciosamente um "
        "termo que não consegue ler.",
    "oversight.sla":
        "{tickets} tickets nesta quinzena; cumprimento do SLA de "
        "primeira resposta {sla}. Uma resposta atrasada é uma quebra "
        "nomeada com um responsável, não um encolher de ombros.",
    "oversight.clocks":
        "Cada relógio operacional — GOP 2h, adjudicação 10d, pagamento "
        "30d, recurso 5d, despacho 2h, reconciliação diária — tem um "
        "responsável nomeado e um denominador visível. Os painéis leem "
        "os relatórios, não impressões.",
    "proof.believe":
        "PORQUE PODE ACREDITAR NISTO. Toda esta quinzena é uma chamada "
        "de função: seed_pilot(seed={seed}). Mesma semente, mesmo piloto, "
        "byte a byte — uma demonstração que não se consegue reproduzir "
        "é uma história que não se consegue auditar.",
    "proof.suites":
        "Cada afirmação nestes slides é sustentada por um módulo com a "
        "sua própria bateria de testes; a plataforma corre a bateria "
        "completa na etiqueta de implantação antes de um portão virar.",
    "proof.environment":
        "O que ainda é ambiente, e dizemo-lo com honestidade: o contrato "
        "PSP ao vivo, a cerimónia HSM e o kit de corredor provam-se nos "
        "portões de entrada em produção G0–G5, não nesta demonstração.",
}

# Scene frames in PT: surfaces, silent questions, and the cabo-verde
# stakes. Keyed by scene id so a scene builder change never invalidates
# a translation silently — a missing key falls back, stated.
PT_FRAMES = {
    "surface": {
        "arrival": "consola do corredor fronteiriço — vista do funcionário",
        "verdict": "consola do corredor fronteiriço — ecrã de veredito",
        "money": "painel financeiro — dotações do fundo + reconciliação",
        "care": "portal do prestador + bancada de sinistros",
        "rescue": "consola de assistência — quadro de evacuações",
        "surveillance": "registo de saúde pública — lista de alertas BR-10",
        "oversight": "consola de apoio + painel de relógios operacionais",
        "proof": "o terminal — um comando, uma semente",
    },
    "question": {
        "arrival": "quem são estas pessoas e o sistema conhece-as?",
        "verdict": "o que impede um QR falso no corredor?",
        "money": "para onde vai o dinheiro e quem lhe pode mexer?",
        "care": "um viajante doente é mesmo tratado?",
        "rescue": "o que acontece no pior dia?",
        "surveillance": "o ministério saberia antes do surto?",
        "oversight": "quem responde quando o relógio falha?",
        "proof": "porque havemos de acreditar nisto?",
    },
}

PT_STAKES_CV = {
    "arrival": "O turismo é a economia: cada chegada pelo Sal, Praia ou "
               "São Vicente é receita coberta e uma obrigação de saúde "
               "no mesmo balcão.",
    "verdict": "O corredor no Amílcar Cabral é a fronteira: um QR que se "
               "verifica offline durante 72 horas sobrevive à rede "
               "inter-ilhas melhor do que qualquer sistema sempre-online.",
    "rescue": "Em nove ilhas habitadas, o pior dia é uma evacuação "
              "inter-ilhas — asa fixa, helicóptero ou barco — e o "
              "relógio de despacho de 2 horas é a promessa que importa.",
    "money": "O tesouro pergunta onde estão os escudos: cinco dotações, "
             "uma divisão versionada e um ficheiro de liquidação que "
             "reconcilia diariamente contra o PSP.",
    "care": "Um visitante adoece em Santo Antão; o GOP dentro de 2 horas "
            "é o que o faz ser tratado antes da decisão do ferry.",
    "surveillance": "A saúde portuária é saúde nacional: cinco casos GI "
                    "numa unidade numa semana são um sinal relevante para "
                    "o RSI com pacote de exportação, não um boato.",
    "oversight": "Os visitantes precisam de ajuda em PT, EN, FR, DE, NL — "
                 "uma tradução em falta recua com o recuo declarado, "
                 "nunca em silêncio.",
    "proof": "A quinzena piloto volta a correr de uma só semente em "
             "frente ao ministério — uma demonstração que não se consegue "
             "reproduzir é uma história que não se consegue auditar.",
}


FR_TEMPLATES = {
    "arrival.volume":
        "PREMIER JOUR, COULOIR TROIS. {arrivals} voyageurs arrivent sur "
        "{days} jours — tous synthétiques (Pax-NNNN, SIM-NNNN ; cette "
        "démonstration ne correspond à aucune personne, et le registre "
        "peut le prouver).",
    "arrival.covers":
        "Chacun achète ou détient une couverture : {covers} couvertures "
        "émises, dont {exempt} exemptions légales — un diplomate et un "
        "passager en transit ne sont pas des revenus, et le système ne "
        "les traite jamais comme tels.",
    "arrival.qr":
        "Pointez le QR sur l'écran de l'agent : JWS compact, signé par "
        "la clé détenue dans le HSM, vérifiable hors ligne pendant 72 "
        "heures. Un cache périmé affiche ROUGE, pas un passage "
        "silencieux.",
    "verdict.decision":
        "LE VERDICT EST UNE DÉCISION, PAS UNE INTUITION. Chaque scan "
        "renvoie vert, orange ou rouge avec un *motif nommé* et une "
        "action nommée de l'agent — admettre, guichet ou superviseur.",
    "verdict.fortnight":
        "Sur cette quinzaine : {green} verts, {amber} oranges "
        "(vérification au guichet, résolue sur place), {red} rouges. "
        "Dénominateurs à l'écran, toujours.",
    "verdict.privacy":
        "L'agent ne voit jamais un numéro de passeport en clair — la "
        "vérification au guichet fonctionne à partir de la référence de "
        "couverture.",
    "money.five":
        "SUIVEZ L'ARGENT — LES CINQ CAISSES. Prime collectée : "
        "{premium}. Elle se répartit au moment de la vente entre les "
        "caisses réserve, sinistres, opérations, surveillance et "
        "administration selon le profil pays versionné — une règle "
        "introuvable dans une table est un pot-de-vin avec reçu.",
    "money.channel":
        "Mix de canaux : {partner_sales} ventes via l'API partenaire, "
        "{groups} manifestes de groupe (compagnie aérienne, "
        "tour-opérateur) — tout-ou-rien, tarifé par personne.",
    "money.refunds":
        "Les doubles débits se remboursent d'eux-mêmes : {auto_refunds} "
        "remboursements automatiques sur cette quinzaine ; le premier "
        "débit est conservé, les autres repartent dans la fenêtre.",
    "money.settlement":
        "Le règlement se réconcilie quotidiennement : "
        "{settlement_breaches} écarts contre une tolérance de 1000 ppm. "
        "Zéro est la réponse attendue, et le rapport affiche le "
        "dénominateur.",
    "care.sick":
        "QUAND QUELQU'UN TOMBE MALADE. Un voyageur entre dans une "
        "clinique accréditée ; le portail prestataire ne voit que ses "
        "propres dossiers.",
    "care.clock":
        "{claims} sinistres sur cette quinzaine. L'horloge qui compte : "
        "GOP sous 2 heures après l'admission — la garantie de paiement "
        "est ce qui fait soigner le voyageur sans carte bancaire sur un "
        "brancard.",
    "care.adjudicated":
        "Adjudicé contre le tarifaire à quatre yeux ; payé {paid}. "
        "Ratio sinistres/primes {loss_ratio} — le mart actuariel lit les "
        "mêmes registres, donc la finance et les opérations ne se "
        "disputent jamais sur le bon chiffre.",
    "rescue.worst":
        "LE PIRE JOUR. Une demande d'évacuation sanitaire : approuver, "
        "dépêcher sous l'horloge de 2 heures, arriver, clôturer. Sur "
        "cette quinzaine : {medevacs} évacuation(s).",
    "rescue.uninsured":
        "Si un voyageur *non assuré* doit être secouru, le dispositif "
        "ne le laisse pas mourir au bord d'un tableur : le fonds des "
        "non-assurés porte le coût, et la subrogation poursuit le "
        "recouvrement ensuite — bornée au montant réellement dû, "
        "jamais un chèque en blanc.",
    "surveillance.watches":
        "LE SYSTÈME SE SURVEILLE LUI-MÊME. Chaque incident est "
        "pseudonymisé à l'enregistrement — hachage salé, aucune "
        "identité réversible dans le registre. Synthétique ici, protégé "
        "en production.",
    "surveillance.br10":
        "BR-10 : cinq cas GI dans un établissement en une semaine "
        "déclenchent une alerte. Sur cette quinzaine : {br10_alerts} "
        "alerte(s), acquittée : {ack}. Une alerte non acquittée est un "
        "constat, pas une statistique.",
    "surveillance.export":
        "Le paquet d'export RSI/UKHSA/ECDC porte un SHA-256 — la somme "
        "de contrôle est le contrat — et la suppression au seuil k=5 "
        "garantit que les petites cellules ne révèlent jamais une "
        "personne. Empreinte du paquet : {pack_hash}…",
    "oversight.phone":
        "QUI DÉCROCHE LE TÉLÉPHONE. Les voyageurs ont une assistance "
        "24/7 avec du contenu dans leur langue ; une traduction "
        "manquante bascule *avec le basculement déclaré* — personne ne "
        "signe silencieusement une décharge qu'il ne peut lire.",
    "oversight.sla":
        "{tickets} tickets sur cette quinzaine ; conformité du SLA de "
        "première réponse {sla}. Une réponse tardive est un manquement "
        "nommé avec un responsable, pas un haussement d'épaules.",
    "oversight.clocks":
        "Chaque horloge opérationnelle — GOP 2h, adjudication 10j, "
        "paiement 30j, appel 5j, dépêchement 2h, réconciliation "
        "quotidienne — a un responsable nommé et un dénominateur "
        "visible. Les tableaux de bord lisent les rapports, pas les "
        "impressions.",
    "proof.believe":
        "POURQUOI POUVEZ-VOUS Y CROIRE. Toute cette quinzaine est un "
        "appel de fonction : seed_pilot(seed={seed}). Même graine, même "
        "pilote, octet par octet — une démo qu'on ne peut reproduire "
        "est une histoire qu'on ne peut auditer.",
    "proof.suites":
        "Chaque affirmation de ces diapositives est adossée à un module "
        "avec sa propre suite de tests ; la plateforme exécute la suite "
        "complète sur le tag de déploiement avant qu'un portail ne "
        "bascule.",
    "proof.environment":
        "Ce qui reste de l'environnement, et nous le disons "
        "honnêtement : le contrat PSP réel, la cérémonie HSM et le kit "
        "de couloir se prouvent dans les portails de mise en production "
        "G0–G5, pas dans cette démo.",
}

FR_FRAMES = {
    "surface": {
        "arrival": "console du couloir frontalier — vue agent",
        "verdict": "console du couloir frontalier — écran de verdict",
        "money": "tableau financier — caisses du fonds + réconciliation",
        "care": "portail prestataire + atelier sinistres",
        "rescue": "console d'assistance — tableau des évacuations",
        "surveillance": "registre de santé publique — alertes BR-10",
        "oversight": "console d'assistance + horloges opérationnelles",
        "proof": "le terminal — une commande, une graine",
    },
    "question": {
        "arrival": "qui sont ces personnes et le système les connaît-il ?",
        "verdict": "qu'est-ce qui arrête un faux QR au couloir ?",
        "money": "où va l'argent et qui peut y toucher ?",
        "care": "un voyageur malade est-il vraiment soigné ?",
        "rescue": "que se passe-t-il le pire jour ?",
        "surveillance": "la CEDEAO saurait-elle avant l'épidémie ?",
        "oversight": "qui répond quand l'horloge dépasse ?",
        "proof": "pourquoi devrions-nous y croire ?",
    },
}

FR_STAKES_ECOWAS = {
    "arrival": "La mobilité régionale est le moteur : chaque arrivée par "
               "les corridors Abidjan–Lagos ou Dakar–Bamako est un "
               "revenu couvert et une obligation sanitaire au même "
               "guichet.",
    "verdict": "Le poste frontière est le couloir : un QR vérifiable "
               "hors ligne pendant 72 heures survit aux réseaux "
               "intermittents des corridors terrestres.",
    "rescue": "Le pire jour régional est une évacuation transfrontalière "
              "— et l'horloge de dépêchement de 2 heures est la promesse "
              "qui compte.",
    "money": "Le trésor demande où sont les francs : cinq caisses, une "
             "répartition versionnée, un fichier de règlement réconcilié "
             "quotidiennement contre le PSP.",
    "care": "Un voyageur tombe malade loin de chez lui ; le GOP sous 2 "
            "heures est ce qui le fait soigner avant la question du "
            "transport.",
    "surveillance": "La santé frontalière est la santé régionale : cinq "
                    "cas GI dans un établissement en une semaine sont un "
                    "signal RSI avec paquet d'export, pas une rumeur.",
    "oversight": "Les voyageurs ont besoin d'aide en FR, EN, PT — une "
                 "traduction manquante bascule avec le basculement "
                 "déclaré, jamais en silence.",
    "proof": "La quinzaine pilote se relance d'une seule graine devant "
             "la commission — une démo qu'on ne peut reproduire est une "
             "histoire qu'on ne peut auditer.",
}

# Language registry — adding a language is adding an entry here, never
# an if-lang branch in the builders. Each entry: point templates, scene
# frames, per-audience stakes, and the stated-fallback note.
TRANSLATIONS = {
    "pt": {"templates": PT_TEMPLATES, "frames": PT_FRAMES,
           "stakes": {"cabo-verde": PT_STAKES_CV},
           "fallback_note": "sem tradução em português — apresentado em "
                            "inglês, declarado"},
    "fr": {"templates": FR_TEMPLATES, "frames": FR_FRAMES,
           "stakes": {"ecowas": FR_STAKES_ECOWAS},
           "fallback_note": "sans traduction française — présenté en "
                            "anglais, déclaré"},
}

PRESENTER_LANGS = ("en",) + tuple(TRANSLATIONS)


# --- audiences ---------------------------------------------------------
# The engine is audience-agnostic exactly as it is country-agnostic: the
# narrative drives the numbers, the audience drives the *arc* — which
# scene leads, and why the person in the room should care. Adding an
# audience is adding an entry here, never an if-audience branch in a
# scene builder.

AUDIENCE_ARCS = {
    # Ministry of health: the state watches the border, then the money.
    "ministry": SCENES,
    # University (Unilag-style): the room is the vice-chancellor, the
    # bursar, the clinic director, the insurer partners. Lead with the
    # student at the clinic door; end with the proof.
    "campus": ("arrival", "care", "surveillance", "money",
               "oversight", "rescue", "verdict", "proof"),
    # Cabo Verde: the room is the ministry and the tourism economy of an
    # archipelago. Rescue moves up — on nine inhabited islands the worst
    # day is an inter-island medevac, not an ambulance ride — and the
    # money scene answers the question a tourism-dependent treasury
    # actually asks. Escudo display rides the BCV peg (alt_currency in
    # the cv-vhc profile), never a hardcoded rate.
    "cabo-verde": ("arrival", "verdict", "rescue", "money", "care",
                   "surveillance", "oversight", "proof"),
    # ECOWAS: the room is a regional commission — fifteen member states,
    # cross-border corridors, a shared surveillance mandate. Surveillance
    # moves up: a regional body's first question is whether it sees the
    # outbreak before the communique. Care before money: the treatment
    # guarantee is the political sell; the treasury follows.
    "ecowas": ("arrival", "surveillance", "care", "rescue", "money",
               "verdict", "oversight", "proof"),
}

# One framing line per (audience, scene): why this room cares. Missing
# entries fall back to the audience question — absence of a frame is
# honest, absence of a number is not.
_AUDIENCE_STAKES = {
    "campus": {
        "arrival": "Every international student your registry admits is a "
                   "cover the scheme must honour — and a fee the bursar "
                   "can reconcile.",
        "care": "The campus clinic is the provider portal: a sick student "
                "is treated on a guarantee of payment, not on a cash "
                "deposit at the door.",
        "surveillance": "A dormitory is a facility: five GI cases in one "
                        "hall in one week is a BR-10 alert before it is "
                        "a campus outbreak.",
        "money": "The bursar sees the five buckets and the daily "
                 "settlement recon — the premium split is a table, not "
                 "a promise.",
        "oversight": "Students call the helpdesk at 2am; the SLA clock "
                     "does not care what time it is.",
        "rescue": "The worst day on campus is a medevac; the dispatch "
                  "clock and the uninsured fund are the answer.",
        "verdict": "Registration week is a border lane: QR verdicts keep "
                   "a fraudulent cover out of the clinic queue.",
        "proof": "Your council can rerun this entire fortnight from one "
                 "seed — the demo is auditable because it is reproducible.",
    },
    "cabo-verde": {
        "arrival": "Tourism is the economy: every arrival through Sal, "
                   "Praia or São Vicente is covered revenue and a health "
                   "obligation at the same counter.",
        "verdict": "The lane at Amílcar Cabral is the border: a QR that "
                   "verifies offline for 72 hours survives the inter-island "
                   "network better than any always-online system.",
        "rescue": "On nine inhabited islands the worst day is an "
                  "inter-island medevac — fixed-wing, rotary or boat — "
                  "and the 2-hour dispatch clock is the promise that "
                  "matters.",
        "money": "The treasury asks where the escudos sit: five buckets, "
                 "a versioned split, and a settlement file that "
                 "reconciles daily against the PSP.",
        "care": "A visitor falls ill on Santo Antão; the GOP inside 2 "
                "hours is what gets them treated before the ferry "
                "decision.",
        "surveillance": "Port health is national health: five GI cases "
                        "in one facility in one week is an IHR-relevant "
                        "signal with an export pack, not a rumour.",
        "oversight": "Visitors need help in PT, EN, FR, DE, NL — a "
                     "missing translation falls back with the fallback "
                     "stated, never silently.",
        "proof": "The pilot fortnight reruns from one seed in front of "
                 "the ministry — a demo you cannot reproduce is a story "
                 "you cannot audit.",
    },
    "ecowas": {
        "arrival": "Regional mobility is the engine: every arrival "
                   "through the Abidjan–Lagos or Dakar–Bamako corridors "
                   "is covered revenue and a health obligation at the "
                   "same desk.",
        "surveillance": "Border health is regional health: five GI cases "
                        "in one facility in one week is an IHR-relevant "
                        "signal with an export pack, not a rumour.",
        "care": "A traveller falls ill far from home; the GOP inside 2 "
                "hours is what gets them treated before the transport "
                "question.",
        "rescue": "The worst regional day is a cross-border medevac — "
                  "and the 2-hour dispatch clock is the promise that "
                  "matters.",
        "money": "The treasury asks where the money sits: five buckets, "
                 "a versioned split, and a settlement file that "
                 "reconciles daily against the PSP.",
        "verdict": "The border post is the lane: a QR verifiable offline "
                   "for 72 hours survives the intermittent networks of "
                   "land corridors.",
        "oversight": "Travellers need help in FR, EN, PT — a missing "
                     "translation falls back with the fallback stated, "
                     "never silently.",
        "proof": "The pilot fortnight reruns from one seed in front of "
                 "the commission — a demo you cannot reproduce is a story "
                 "you cannot audit.",
    },
}

_SURFACES = {
    "arrival": "border lane console — officer view",
    "verdict": "border lane console — verdict screen",
    "money": "finance dashboard — fund buckets + settlement recon",
    "care": "provider portal + claims workbench",
    "rescue": "assistance ops console — medevac board",
    "surveillance": "public-health registry — BR-10 alert list",
    "oversight": "helpdesk console + ops clocks dashboard",
    "proof": "the terminal — one command, one seed",
}

_QUESTIONS = {
    "arrival": "who are these people and does the system know them?",
    "verdict": "what stops a bad QR at the lane?",
    "money": "where does the money go and who can touch it?",
    "care": "does a sick traveller actually get treated?",
    "rescue": "what happens on the worst day?",
    "surveillance": "would the ministry know before the outbreak?",
    "oversight": "who is accountable when the clock breaches?",
    "proof": "why should we believe any of this?",
}


def build_walkthrough(narrative: dict, *, currency_symbol: str = "€",
                      audience: str = "ministry", lang: str = "en",
                      rehearse: bool = False) -> dict:
    """Generate the presenter script from a pilot narrative.

    Every number in every line comes from `narrative`. The function
    refuses a narrative missing keys the script needs — a demo with an
    improvised number is worse than no demo. The audience chooses the
    arc (scene order and framing), never the numbers. The language
    chooses the words; a point without a translation falls back to
    English *with the fallback stated and counted* — never silently.

    With rehearse=True each scene carries its declared target seconds
    and the cumulative pace clock, so a rehearsal measures drift
    against a declared plan — not against vibes.
    """
    if audience not in AUDIENCE_ARCS:
        raise ValueError(f"unknown audience {audience!r}; "
                         f"audiences are {tuple(AUDIENCE_ARCS)}")
    if lang not in PRESENTER_LANGS:
        raise ValueError(f"unknown presenter language {lang!r}; "
                         f"languages are {PRESENTER_LANGS}")
    if currency_symbol.lower() in ("cve", "cve$", "escudo"):
        currency_symbol = "CVE"
    required = {"arrivals", "covers", "exempt", "verdicts", "groups",
                "partner_sales", "premium_collected_minor", "auto_refunds",
                "settlement_breaches", "claims", "claims_paid_minor",
                "loss_ratio", "medevacs", "br10_alerts",
                "br10_all_acknowledged", "export_pack_sha256", "tickets",
                "helpdesk_sla", "seed", "days", "version"}
    missing = required - set(narrative)
    if missing:
        raise ValueError(f"narrative is missing {sorted(missing)}; "
                         "the script reads the narrative, it does not improvise")

    stakes = _AUDIENCE_STAKES.get(audience, {})
    translated = 0
    total_points = 0
    fallback_keys = []

    def _localized(key, values, en_text):
        """Resolve one point in the presenter language; a miss is a
        stated fallback, counted — never a silent English paragraph."""
        nonlocal translated, total_points
        total_points += 1
        if lang == "en":
            return en_text
        pack = TRANSLATIONS[lang]
        tpl = pack["templates"].get(key)
        if tpl is not None:
            translated += 1
            return tpl.format(**values)
        fallback_keys.append(key)
        return en_text + f"  _({pack['fallback_note']})_"

    scenes = []
    for sid in AUDIENCE_ARCS[audience]:
        points = _BUILDERS[sid](narrative, currency_symbol)
        keys = [k for k, _, _ in points]
        texts = [_localized(k, vals, text) for k, text, vals in points]
        if lang == "en":
            surface, question = _SURFACES[sid], _QUESTIONS[sid]
        else:
            frames = TRANSLATIONS[lang]["frames"]
            surface = frames["surface"].get(sid, _SURFACES[sid])
            question = frames["question"].get(sid, _QUESTIONS[sid])
        scene = {
            "scene": sid,
            "surface": surface,
            "audience_question": question,
            "talking_points": texts,
            "talking_point_keys": keys,
        }
        if sid in stakes:
            if lang == "en":
                scene["stakes"] = stakes[sid]
            else:
                pack = TRANSLATIONS[lang]
                loc_stake = pack["stakes"].get(audience, {}).get(sid)
                if loc_stake is not None:
                    scene["stakes"] = loc_stake
                else:
                    fallback_keys.append(f"stakes.{audience}.{sid}")
                    scene["stakes"] = stakes[sid] + \
                        f"  _({pack['fallback_note']})_"
        scenes.append(scene)
    result = {
        "version": DEMO_VERSION,
        "pilot_version": narrative["version"],
        "seed": narrative["seed"],
        "audience": audience,
        "lang": lang,
        "scenes": scenes,
        "translation": {"lang": lang, "translated": translated,
                        "total": total_points,
                        "fallback_keys": fallback_keys},
        "disclaimer": (("Todos os viajantes, passaportes, prestadores e "
                        "valores nesta apresentação são sintéticos "
                        "(Pax-NNNN / SIM-NNNN). A demonstração não "
                        "corresponde a nenhuma pessoa.")
                       if lang == "pt" else
                       ("Tous les voyageurs, passeports, prestataires et "
                        "montants de cette présentation sont synthétiques "
                        "(Pax-NNNN / SIM-NNNN). La démonstration ne "
                        "correspond à aucune personne.")
                       if lang == "fr" else
                       ("All travellers, passports, providers and money in "
                        "this walkthrough are synthetic (Pax-NNNN / SIM-NNNN). "
                        "The demonstration maps to no person.")),
    }
    if rehearse:
        elapsed = 0
        pace = []
        for s in result["scenes"]:
            target = SCENE_TIMING[s["scene"]]
            s["target_seconds"] = target
            s["opens_at_seconds"] = elapsed
            pace.append({"scene": s["scene"], "target_seconds": target,
                         "opens_at_seconds": elapsed})
            elapsed += target
        result["rehearsal"] = {"total_seconds": elapsed, "pace": pace}
    return result


# --- demo board: scenes wired to real PWA surfaces ----------------------
# A scene names the surface the presenter stands on; the board makes that
# a link. Only surfaces that EXIST are linked — a scene without a surface
# says so honestly, because a cable not plugged in is not an interface.
SCENE_SURFACES = {
    "arrival": ("vhc-verify.html", "border verification console"),
    "verdict": ("vhc-verify.html", "border verification console"),
    "care": ("traveler.html", "traveler medical pass"),
    "rescue": ("dispatch.html", "dispatch board"),
    "money": ("vhc-fund.html", "fund buckets dashboard"),
    "surveillance": ("vhc-br10.html", "BR-10 alert list"),
    "oversight": ("vhc-helpdesk.html", "helpdesk console"),
    "proof": None,          # the terminal — no surface, by design
}


def build_board(walkthrough: dict, narrative: dict) -> dict:
    """Wire a walkthrough to the real PWA surfaces. The board is data:
    a page renders it, a test audits it. Red findings ride the board —
    a demo board that hides a red finding is complicit."""
    red = []
    if not narrative["br10_all_acknowledged"]:
        red.append("BR-10 alert unacknowledged")
    if narrative["settlement_breaches"] > 0:
        red.append(f"{narrative['settlement_breaches']} settlement breach(es)")
    scenes = []
    for s in walkthrough["scenes"]:
        link = SCENE_SURFACES.get(s["scene"])
        entry = {
            "scene": s["scene"],
            "surface": s["surface"],
            "link": link[0] if link else None,
            "link_label": link[1] if link else None,
            "unwired": link is None,
            "talking_points": s["talking_points"],
        }
        if "target_seconds" in s:
            entry["target_seconds"] = s["target_seconds"]
            entry["opens_at_seconds"] = s["opens_at_seconds"]
        scenes.append(entry)
    board = {
        "version": walkthrough["version"],
        "seed": walkthrough["seed"],
        "audience": walkthrough.get("audience", "ministry"),
        "lang": walkthrough.get("lang", "en"),
        "disclaimer": walkthrough["disclaimer"],
        "red_findings": red,
        "scenes": scenes,
        "narrative": narrative,
    }
    if "rehearsal" in walkthrough:
        board["rehearsal"] = walkthrough["rehearsal"]
    return board


def render_markdown(walkthrough: dict) -> str:
    """Render the script as a presenter-ready Markdown run-of-show."""
    lang = walkthrough.get("lang", "en")
    _LABELS = {
        "en": {"stand_on": "Stand on", "asking": "They are silently asking",
               "stakes": "Why this room cares", "scene": "Scene",
               "title": "# VHC scheme — {aud} walkthrough",
               "coverage": "translation coverage: {t} of {n} points in "
                           "{lang}; stated fallbacks: {f}"},
        "pt": {"stand_on": "Posicione-se em", "asking": "A pergunta silenciosa",
               "stakes": "Porque esta sala se importa", "scene": "Cena",
               "title": "# Esquema VHC — apresentação {aud}",
               "coverage": "cobertura de tradução: {t} de {n} pontos em "
                           "português; recuos declarados: {f}"},
        "fr": {"stand_on": "Positionnez-vous sur",
               "asking": "La question silencieuse",
               "stakes": "Pourquoi cette salle s'en soucie", "scene": "Scène",
               "title": "# Dispositif VHC — présentation {aud}",
               "coverage": "couverture de traduction : {t} points sur {n} "
                           "en français ; basculements déclarés : {f}"},
    }
    labels = _LABELS[lang]
    title = labels["title"].format(aud=walkthrough.get("audience", "ministry"))
    lines = [title,
             f"",
             f"_{walkthrough['disclaimer']}_",
             f"",
             f"pilot `{walkthrough['pilot_version']}` · seed "
             f"`{walkthrough['seed']}` · script `{walkthrough['version']}`",
             ""]
    tr = walkthrough.get("translation")
    if tr and tr["lang"] != "en":
        lines.append("_" + labels["coverage"].format(
            t=tr["translated"], n=tr["total"], lang=tr["lang"],
            f=len(tr["fallback_keys"])) + "_")
        lines.append("")
    if "rehearsal" in walkthrough:
        lines.append(f"_rehearsal pace: "
                     f"{walkthrough['rehearsal']['total_seconds']}s total, "
                     "declared per scene — measure drift against the plan_")
        lines.append("")
    for i, s in enumerate(walkthrough["scenes"], 1):
        lines.append(f"## {labels['scene']} {i}: {s['scene']}")
        if "target_seconds" in s:
            lines.append(f"⏱ {s['target_seconds']}s "
                         f"(opens at {s['opens_at_seconds']}s)")
        lines.append(f"**{labels['stand_on']}:** {s['surface']}")
        lines.append(f"**{labels['asking']}:** {s['audience_question']}")
        if "stakes" in s:
            lines.append(f"**{labels['stakes']}:** {s['stakes']}")
        lines.append("")
        for tp in s["talking_points"]:
            lines.append(f"- {tp}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    """One-command ministry demo: seed the pilot, build the script,
    render the run-of-show. Refuses to present past a red finding —
    an unacknowledged BR-10 alert or a settlement breach exits 1.

      PYTHONPATH=packages/travelpass:packages/crosslink:... \\
          python3 -m vhdemo --seed 42 --out docs/demo/ministry-walkthrough.md
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="vhdemo",
                                 description="VHC ministry walkthrough generator")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--currency", default="€")
    ap.add_argument("--audience", default="ministry",
                    choices=sorted(AUDIENCE_ARCS),
                    help="ministry of health or campus (university council)")
    ap.add_argument("--lang", default="en", choices=PRESENTER_LANGS,
                    help="presenter language; pt falls back to en, stated")
    ap.add_argument("--out", default=None,
                    help="write the Markdown run-of-show here")
    ap.add_argument("--board-out", default=None,
                    help="write the PWA demo board JSON here (write it into "
                         "apps/pwa/ so linked surfaces resolve)")
    ap.add_argument("--json", action="store_true",
                    help="print the narrative as JSON instead of the script")
    ap.add_argument("--rehearse", action="store_true",
                    help="stamp the declared per-scene pace (seconds) into "
                         "the script and board for rehearsal timing")
    args = ap.parse_args(argv)

    from vhpilot import seed_pilot  # late import: needs the sys.path
    narrative = seed_pilot(seed=args.seed, days=args.days)["narrative"]
    script = build_walkthrough(narrative, currency_symbol=args.currency,
                               audience=args.audience, lang=args.lang,
                               rehearse=args.rehearse)

    # You do not present past a red finding.
    red = []
    if not narrative["br10_all_acknowledged"]:
        red.append("BR-10 alert unacknowledged")
    if narrative["settlement_breaches"] > 0:
        red.append(f"{narrative['settlement_breaches']} settlement breach(es)")

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(render_markdown(script))
        print(f"walkthrough written to {args.out}")

    if args.board_out:
        import os as _os
        board = build_board(script, narrative)
        # linked surfaces must exist — the board refuses a dead link
        board_dir = _os.path.dirname(_os.path.abspath(args.board_out))
        dead = [s["link"] for s in board["scenes"]
                if s["link"] and not _os.path.exists(
                    _os.path.join(board_dir, s["link"]))]
        if dead:
            print(f"dead surface links refused: {sorted(set(dead))}")
            return 1
        # the dashboard surfaces read registry payloads — export them
        # alongside the board so a linked surface never renders empty
        from vhdash import export_dashboards
        from vhpilot import seed_pilot as _seed
        board_sym = "CVE" if args.currency.lower() in ("cve", "cve$", "escudo") \
            else args.currency
        registries = _seed(seed=args.seed, days=args.days)["registries"]
        for name, path in export_dashboards(
                registries, board_dir,
                currency_symbol=board_sym).items():
            print(f"dashboard payload written to {path}")
        # the growth surface reads its own seeded book — same command,
        # deterministic seed, checksum-stamped like the rest
        from vhdashx import export_growth, seed_growth
        print(f"dashboard payload written to "
              f"{export_growth(seed_growth(), board_dir)}")
        with open(args.board_out, "w") as fh:
            json.dump(board, fh, indent=2, ensure_ascii=False)
        print(f"demo board written to {args.board_out}")

    if args.json:
        print(json.dumps(narrative, indent=2, sort_keys=True))
    else:
        n = narrative
        sym = "CVE" if args.currency.lower() in ("cve", "cve$", "escudo") \
            else args.currency
        print(f"VHC {args.audience} walkthrough — seed {n['seed']}, {n['days']} days")
        print(f"  {n['arrivals']} arrivals · {n['covers']} covers "
              f"({n['exempt']} exempt) · {n['partner_sales']} partner sales "
              f"· {n['groups']} group manifests")
        print(f"  premium {_fmt_minor(n['premium_collected_minor'], sym)} "
              f"· claims {n['claims']} paid "
              f"{_fmt_minor(n['claims_paid_minor'], sym)} "
              f"· loss ratio {n['loss_ratio']:.4f}")
        print(f"  medevacs {n['medevacs']} · BR-10 alerts {n['br10_alerts']} "
              f"(acknowledged: {n['br10_all_acknowledged']}) · "
              f"settlement breaches {n['settlement_breaches']}")
        print(f"  scenes: {' -> '.join(s['scene'] for s in script['scenes'])}")
    if red:
        print("STOP THE DEMO: " + "; ".join(red))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
