# Esquema VHC — apresentação cabo-verde

_Todos os viajantes, passaportes, prestadores e valores nesta apresentação são sintéticos (Pax-NNNN / SIM-NNNN). A demonstração não corresponde a nenhuma pessoa._

pilot `vhc-pilot@1.0.0` · seed `42` · script `vhc-demo@1.0.0`

_cobertura de tradução: 24 de 24 pontos em português; recuos declarados: 0_

## Cena 1: arrival
**Posicione-se em:** consola do corredor fronteiriço — vista do funcionário
**A pergunta silenciosa:** quem são estas pessoas e o sistema conhece-as?
**Porque esta sala se importa:** O turismo é a economia: cada chegada pelo Sal, Praia ou São Vicente é receita coberta e uma obrigação de saúde no mesmo balcão.

- DIA UM, CORREDOR TRÊS. 328 viajantes chegam ao longo de 14 dias — todos sintéticos (Pax-NNNN, SIM-NNNN; esta demonstração não corresponde a nenhuma pessoa, e o registo consegue prová-lo).
- Cada um compra ou traz cobertura: 328 coberturas emitidas, das quais 2 são isenções legais — um diplomata e um passageiro em trânsito não são receita, e o sistema nunca os trata como tal.
- Aponte para o QR no ecrã do funcionário: JWS compacto, assinado pela chave guardada no HSM, verificável offline durante 72 horas. Uma cache expirada mostra VERMELHO, não um passe silencioso.

## Cena 2: verdict
**Posicione-se em:** consola do corredor fronteiriço — ecrã de veredito
**A pergunta silenciosa:** o que impede um QR falso no corredor?
**Porque esta sala se importa:** O corredor no Amílcar Cabral é a fronteira: um QR que se verifica offline durante 72 horas sobrevive à rede inter-ilhas melhor do que qualquer sistema sempre-online.

- O VEREDITO É UMA DECISÃO, NÃO UMA INTUIÇÃO. Cada leitura devolve verde, âmbar ou vermelho com um *motivo nomeado* e uma ação nomeada do funcionário — admitir, balcão ou supervisor.
- Nesta quinzena: 127 verdes, 0 âmbar (consulta ao balcão, resolvido no momento), 0 vermelhos. Denominadores no ecrã, sempre.
- O funcionário nunca vê um número de passaporte em claro — a consulta ao balcão funciona a partir da referência da cobertura.

## Cena 3: rescue
**Posicione-se em:** consola de assistência — quadro de evacuações
**A pergunta silenciosa:** o que acontece no pior dia?
**Porque esta sala se importa:** Em nove ilhas habitadas, o pior dia é uma evacuação inter-ilhas — asa fixa, helicóptero ou barco — e o relógio de despacho de 2 horas é a promessa que importa.

- O PIOR DIA. Um pedido de evacuação médica: aprovar, despachar dentro do relógio de 2 horas, chegar, encerrar. Nesta quinzena: 1 evacuação(ões).
- Se um viajante *sem seguro* precisar de resgate, o esquema não o deixa morrer na fronteira de uma folha de cálculo: o fundo de não segurados regista o custo e a sub-rogação procura a recuperação depois — limitada ao que está realmente em dívida, nunca um cheque em branco.

## Cena 4: money
**Posicione-se em:** painel financeiro — dotações do fundo + reconciliação
**A pergunta silenciosa:** para onde vai o dinheiro e quem lhe pode mexer?
**Porque esta sala se importa:** O tesouro pergunta onde estão os escudos: cinco dotações, uma divisão versionada e um ficheiro de liquidação que reconcilia diariamente contra o PSP.

- SIGA O DINHEIRO — AS CINCO VERBAS. Prémio cobrado: 1.026.842$81. Divide-se no momento da venda em dotações de reserva, sinistros, operações, vigilância e administração conforme o perfil de país versionado — uma regra que não pode ser rastreada até uma tabela é um suborno com recibo.
- Mistura de canais: 65 vendas pela API de parceiros, 3 manifestos de grupo (companhia aérea, operador turístico) — tudo-ou-nada, com preço por pessoa.
- Cobranças duplicadas reembolsam-se a si próprias: 0 reembolsos automáticos nesta quinzena; a primeira cobrança fica, as restantes voltam dentro da janela.
- A liquidação reconcilia diariamente: 0 quebras contra a tolerância de 1000 ppm. Zero é a resposta esperada, e o relatório mostra o denominador.

## Cena 5: care
**Posicione-se em:** portal do prestador + bancada de sinistros
**A pergunta silenciosa:** um viajante doente é mesmo tratado?
**Porque esta sala se importa:** Um visitante adoece em Santo Antão; o GOP dentro de 2 horas é o que o faz ser tratado antes da decisão do ferry.

- QUANDO ALGUÉM ADOECE. Um viajante entra numa clínica credenciada; o portal do prestador vê apenas os seus próprios casos.
- 10 sinistros nesta quinzena. O relógio que importa: GOP dentro de 2 horas após a admissão — a garantia de pagamento é o que faz o viajante ser tratado sem cartão de crédito numa maca.
- Adjudicado contra a tabela de tarifas a quatro olhos; pago 77.185$50. Rácio de sinistralidade 0.0723 — o mart atuarial lê os mesmos registos, por isso finanças e operações nunca discutem sobre qual número é real.

## Cena 6: surveillance
**Posicione-se em:** registo de saúde pública — lista de alertas BR-10
**A pergunta silenciosa:** o ministério saberia antes do surto?
**Porque esta sala se importa:** A saúde portuária é saúde nacional: cinco casos GI numa unidade numa semana são um sinal relevante para o RSI com pacote de exportação, não um boato.

- O SISTEMA VIGIA-SE A SI PRÓPRIO. Cada incidente é pseudonimizado no registo — hash com sal, sem identidade reversível no registo. Sintético aqui, protegido em produção.
- BR-10: cinco casos GI numa unidade numa semana disparam um alerta. Nesta quinzena: 1 alerta(s), reconhecido: yes. Um alerta não reconhecido é uma constatação, não uma estatística.
- O pacote de exportação IHR/UKHSA/ECDC traz um SHA-256 — o checksum é o contrato — e a supressão com limiar k=5 garante que células pequenas nunca revelam uma pessoa. Hash do pacote: 1a1f2fa3ce1bc77f…

## Cena 7: oversight
**Posicione-se em:** consola de apoio + painel de relógios operacionais
**A pergunta silenciosa:** quem responde quando o relógio falha?
**Porque esta sala se importa:** Os visitantes precisam de ajuda em PT, EN, FR, DE, NL — uma tradução em falta recua com o recuo declarado, nunca em silêncio.

- QUEM ATENDE O TELEFONE. Os viajantes têm um balcão de apoio 24/7 com conteúdo na sua língua; uma tradução em falta recua *com o recuo declarado* — ninguém assina silenciosamente um termo que não consegue ler.
- 8 tickets nesta quinzena; cumprimento do SLA de primeira resposta {'compliant': 8, 'responded': 8}. Uma resposta atrasada é uma quebra nomeada com um responsável, não um encolher de ombros.
- Cada relógio operacional — GOP 2h, adjudicação 10d, pagamento 30d, recurso 5d, despacho 2h, reconciliação diária — tem um responsável nomeado e um denominador visível. Os painéis leem os relatórios, não impressões.

## Cena 8: proof
**Posicione-se em:** o terminal — um comando, uma semente
**A pergunta silenciosa:** porque havemos de acreditar nisto?
**Porque esta sala se importa:** A quinzena piloto volta a correr de uma só semente em frente ao ministério — uma demonstração que não se consegue reproduzir é uma história que não se consegue auditar.

- PORQUE PODE ACREDITAR NISTO. Toda esta quinzena é uma chamada de função: seed_pilot(seed=42). Mesma semente, mesmo piloto, byte a byte — uma demonstração que não se consegue reproduzir é uma história que não se consegue auditar.
- Cada afirmação nestes slides é sustentada por um módulo com a sua própria bateria de testes; a plataforma corre a bateria completa na etiqueta de implantação antes de um portão virar.
- O que ainda é ambiente, e dizemo-lo com honestidade: o contrato PSP ao vivo, a cerimónia HSM e o kit de corredor provam-se nos portões de entrada em produção G0–G5, não nesta demonstração.
