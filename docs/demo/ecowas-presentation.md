# Dispositif VHC — présentation ecowas

_Tous les voyageurs, passeports, prestataires et montants de cette présentation sont synthétiques (Pax-NNNN / SIM-NNNN). La démonstration ne correspond à aucune personne._

pilot `vhc-pilot@1.0.0` · seed `42` · script `vhc-demo@1.0.0`

_couverture de traduction : 24 points sur 24 en français ; basculements déclarés : 0_

## Scène 1: arrival
**Positionnez-vous sur:** console du couloir frontalier — vue agent
**La question silencieuse:** qui sont ces personnes et le système les connaît-il ?
**Pourquoi cette salle s'en soucie:** La mobilité régionale est le moteur : chaque arrivée par les corridors Abidjan–Lagos ou Dakar–Bamako est un revenu couvert et une obligation sanitaire au même guichet.

- PREMIER JOUR, COULOIR TROIS. 328 voyageurs arrivent sur 14 jours — tous synthétiques (Pax-NNNN, SIM-NNNN ; cette démonstration ne correspond à aucune personne, et le registre peut le prouver).
- Chacun achète ou détient une couverture : 328 couvertures émises, dont 2 exemptions légales — un diplomate et un passager en transit ne sont pas des revenus, et le système ne les traite jamais comme tels.
- Pointez le QR sur l'écran de l'agent : JWS compact, signé par la clé détenue dans le HSM, vérifiable hors ligne pendant 72 heures. Un cache périmé affiche ROUGE, pas un passage silencieux.

## Scène 2: surveillance
**Positionnez-vous sur:** registre de santé publique — alertes BR-10
**La question silencieuse:** la CEDEAO saurait-elle avant l'épidémie ?
**Pourquoi cette salle s'en soucie:** La santé frontalière est la santé régionale : cinq cas GI dans un établissement en une semaine sont un signal RSI avec paquet d'export, pas une rumeur.

- LE SYSTÈME SE SURVEILLE LUI-MÊME. Chaque incident est pseudonymisé à l'enregistrement — hachage salé, aucune identité réversible dans le registre. Synthétique ici, protégé en production.
- BR-10 : cinq cas GI dans un établissement en une semaine déclenchent une alerte. Sur cette quinzaine : 1 alerte(s), acquittée : yes. Une alerte non acquittée est un constat, pas une statistique.
- Le paquet d'export RSI/UKHSA/ECDC porte un SHA-256 — la somme de contrôle est le contrat — et la suppression au seuil k=5 garantit que les petites cellules ne révèlent jamais une personne. Empreinte du paquet : 1a1f2fa3ce1bc77f…

## Scène 3: care
**Positionnez-vous sur:** portail prestataire + atelier sinistres
**La question silencieuse:** un voyageur malade est-il vraiment soigné ?
**Pourquoi cette salle s'en soucie:** Un voyageur tombe malade loin de chez lui ; le GOP sous 2 heures est ce qui le fait soigner avant la question du transport.

- QUAND QUELQU'UN TOMBE MALADE. Un voyageur entre dans une clinique accréditée ; le portail prestataire ne voit que ses propres dossiers.
- 10 sinistres sur cette quinzaine. L'horloge qui compte : GOP sous 2 heures après l'admission — la garantie de paiement est ce qui fait soigner le voyageur sans carte bancaire sur un brancard.
- Adjudicé contre le tarifaire à quatre yeux ; payé €700.00. Ratio sinistres/primes 0.0723 — le mart actuariel lit les mêmes registres, donc la finance et les opérations ne se disputent jamais sur le bon chiffre.

## Scène 4: rescue
**Positionnez-vous sur:** console d'assistance — tableau des évacuations
**La question silencieuse:** que se passe-t-il le pire jour ?
**Pourquoi cette salle s'en soucie:** Le pire jour régional est une évacuation transfrontalière — et l'horloge de dépêchement de 2 heures est la promesse qui compte.

- LE PIRE JOUR. Une demande d'évacuation sanitaire : approuver, dépêcher sous l'horloge de 2 heures, arriver, clôturer. Sur cette quinzaine : 1 évacuation(s).
- Si un voyageur *non assuré* doit être secouru, le dispositif ne le laisse pas mourir au bord d'un tableur : le fonds des non-assurés porte le coût, et la subrogation poursuit le recouvrement ensuite — bornée au montant réellement dû, jamais un chèque en blanc.

## Scène 5: money
**Positionnez-vous sur:** tableau financier — caisses du fonds + réconciliation
**La question silencieuse:** où va l'argent et qui peut y toucher ?
**Pourquoi cette salle s'en soucie:** Le trésor demande où sont les francs : cinq caisses, une répartition versionnée, un fichier de règlement réconcilié quotidiennement contre le PSP.

- SUIVEZ L'ARGENT — LES CINQ CAISSES. Prime collectée : €9,312.50. Elle se répartit au moment de la vente entre les caisses réserve, sinistres, opérations, surveillance et administration selon le profil pays versionné — une règle introuvable dans une table est un pot-de-vin avec reçu.
- Mix de canaux : 65 ventes via l'API partenaire, 3 manifestes de groupe (compagnie aérienne, tour-opérateur) — tout-ou-rien, tarifé par personne.
- Les doubles débits se remboursent d'eux-mêmes : 0 remboursements automatiques sur cette quinzaine ; le premier débit est conservé, les autres repartent dans la fenêtre.
- Le règlement se réconcilie quotidiennement : 0 écarts contre une tolérance de 1000 ppm. Zéro est la réponse attendue, et le rapport affiche le dénominateur.

## Scène 6: verdict
**Positionnez-vous sur:** console du couloir frontalier — écran de verdict
**La question silencieuse:** qu'est-ce qui arrête un faux QR au couloir ?
**Pourquoi cette salle s'en soucie:** Le poste frontière est le couloir : un QR vérifiable hors ligne pendant 72 heures survit aux réseaux intermittents des corridors terrestres.

- LE VERDICT EST UNE DÉCISION, PAS UNE INTUITION. Chaque scan renvoie vert, orange ou rouge avec un *motif nommé* et une action nommée de l'agent — admettre, guichet ou superviseur.
- Sur cette quinzaine : 127 verts, 0 oranges (vérification au guichet, résolue sur place), 0 rouges. Dénominateurs à l'écran, toujours.
- L'agent ne voit jamais un numéro de passeport en clair — la vérification au guichet fonctionne à partir de la référence de couverture.

## Scène 7: oversight
**Positionnez-vous sur:** console d'assistance + horloges opérationnelles
**La question silencieuse:** qui répond quand l'horloge dépasse ?
**Pourquoi cette salle s'en soucie:** Les voyageurs ont besoin d'aide en FR, EN, PT — une traduction manquante bascule avec le basculement déclaré, jamais en silence.

- QUI DÉCROCHE LE TÉLÉPHONE. Les voyageurs ont une assistance 24/7 avec du contenu dans leur langue ; une traduction manquante bascule *avec le basculement déclaré* — personne ne signe silencieusement une décharge qu'il ne peut lire.
- 8 tickets sur cette quinzaine ; conformité du SLA de première réponse {'compliant': 8, 'responded': 8}. Une réponse tardive est un manquement nommé avec un responsable, pas un haussement d'épaules.
- Chaque horloge opérationnelle — GOP 2h, adjudication 10j, paiement 30j, appel 5j, dépêchement 2h, réconciliation quotidienne — a un responsable nommé et un dénominateur visible. Les tableaux de bord lisent les rapports, pas les impressions.

## Scène 8: proof
**Positionnez-vous sur:** le terminal — une commande, une graine
**La question silencieuse:** pourquoi devrions-nous y croire ?
**Pourquoi cette salle s'en soucie:** La quinzaine pilote se relance d'une seule graine devant la commission — une démo qu'on ne peut reproduire est une histoire qu'on ne peut auditer.

- POURQUOI POUVEZ-VOUS Y CROIRE. Toute cette quinzaine est un appel de fonction : seed_pilot(seed=42). Même graine, même pilote, octet par octet — une démo qu'on ne peut reproduire est une histoire qu'on ne peut auditer.
- Chaque affirmation de ces diapositives est adossée à un module avec sa propre suite de tests ; la plateforme exécute la suite complète sur le tag de déploiement avant qu'un portail ne bascule.
- Ce qui reste de l'environnement, et nous le disons honnêtement : le contrat PSP réel, la cérémonie HSM et le kit de couloir se prouvent dans les portails de mise en production G0–G5, pas dans cette démo.
