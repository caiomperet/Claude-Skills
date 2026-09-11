# netmon: monitor leve da conexão de internet

Um app Python sem dependências externas, com interface gráfica e instalador
para Windows, que roda em segundo plano e registra a qualidade da sua conexão ao longo de dias ou semanas. O objetivo é
responder com dados, e não com impressão, a três perguntas:

1. O problema é da rede local (Wi-Fi, cabo, roteador) ou do provedor?
2. O provedor entrega a velocidade contratada, e com que constância?
3. A conexão é instável (quedas, perda de pacotes, latência) ou apenas pequena
   para o uso? Instabilidade pede reclamação ou troca de provedor; falta de
   velocidade com estabilidade pede upgrade.

## O que é medido

| Medição | Frequência padrão | Como | Custo de banda |
|---|---|---|---|
| Latência, jitter e perda de pacotes | a cada 60 s | 10 pings ICMP para 1.1.1.1, 8.8.8.8 e 9.9.9.9 em paralelo (fallback automático para TCP connect se o ICMP estiver bloqueado) | desprezível (~2 KB/min) |
| Latência e perda nos saltos da rede local | a cada 60 s | mesmos pings para cada roteador da casa, descobertos sozinho com traceroute | desprezível |
| Resolução DNS | a cada 5 min | tempo para resolver três nomes | desprezível |
| Download | a cada 30 min | baixa até 10 MB (ou 12 s, o que vier antes) de speed.cloudflare.com | 10 MB |
| Upload | a cada 30 min | envia 4 MB para speed.cloudflare.com | 4 MB |

Com os padrões, o teste de velocidade consome no máximo 14 MB a cada meia hora,
cerca de 670 MB por dia ou 20 GB por mês. As proteções abaixo reduzem isso:

- **Conexão ocupada**: antes de cada teste, o app mede o tráfego real da máquina
  por 2 s. Se passar de `skip_if_busy_mbps` (2 Mbps), o teste é adiado 5 min.
  Assim o teste não compete com uma chamada de vídeo ou um upload seu. No Linux
  isso funciona nativamente; no Windows e no macOS funciona melhor com
  `pip install psutil` (opcional).
- **Horários de silêncio** (`quiet_hours`): por exemplo `["09:00-12:00", "14:00-18:00"]`
  suspende os testes de velocidade nesses períodos. Pings e DNS continuam.
- **Orçamento diário** (`daily_budget_mb`): teto de bytes por dia para os testes
  de velocidade (padrão 1500 MB).

Os pings feitos durante um teste de velocidade são marcados e excluídos das
estatísticas de perda e latência, para não contaminar a medição.

**Onde a perda nasce.** Numa casa com roteador da operadora mais um mesh, há
dois equipamentos entre o computador e a internet. O programa descobre os dois
e mede cada um. Com isso o relatório separa sozinho três origens: perda já no
primeiro salto (o Wi-Fi), perda entre os equipamentos da casa, e perda só
depois de sair de casa (o provedor). Para fixar os endereços à mão, use o
campo "Saltos da rede local" nas Configurações.

**Quando o computador suspende.** O modo chamada grava uma linha por minuto
mesmo quando nada responde, então falta de linhas significa processo parado, e
não rede parada. Esses intervalos são detectados e marcados como artefato, sem
apagar nada, para não virarem "travamentos" de horas. Deixe a suspensão em
"Nunca" nas opções de energia para a cobertura ficar contínua.

Uma nota sobre os números de velocidade: numa conexão rápida, um arquivo
pequeno termina antes de o TCP acelerar, e o tempo até o primeiro byte domina
a conta. Por isso a medição descarta o meio segundo inicial e, se ainda assim
a janela medida ficar curta, o teste seguinte dobra de tamanho, até o limite
de `max_download_bytes`. O intervalo entre testes é esticado sozinho para
caber em `daily_budget_mb`, então aumentar o tamanho não aumenta o consumo.
Medições feitas antes de a janela ser suficiente ficam marcadas, e o
diagnóstico se recusa a julgar o plano enquanto elas dominarem a amostra.

## Instalação no Windows sem digitar nada

Há duas formas; as duas criam um atalho "netmon" na Área de Trabalho e no Menu
Iniciar, ligam o início automático com o Windows, iniciam o monitor em segundo
plano e abrem a interface.

**Opção A: instalador `netmon-setup.exe`.** Baixe o arquivo em *Releases* do
repositório (ou em *Actions*, artefato `netmon-setup`, na execução mais
recente) e dê dois cliques. Não exige Python nem administrador; instala em
`%LOCALAPPDATA%\Programs\netmon`. O Windows pode mostrar o aviso "aplicativo
não reconhecido" por o executável não ser assinado; clique em "Mais
informações" e "Executar assim mesmo". Para desinstalar, use "Aplicativos
instalados" nas Configurações do Windows.

**Opção B: `Instalar.bat`.** Baixe a pasta `netmon` (ou o repositório inteiro
como ZIP e descompacte) e dê dois cliques em `Instalar.bat`. Se o Python não
estiver instalado, ele baixa o instalador oficial de python.org e instala em
modo silencioso, sem perguntas. Depois copia o programa para
`%LOCALAPPDATA%\Programs\netmon` e faz o resto.

Em qualquer das opções, a única coisa a preencher depois é a velocidade do
plano contratado, na seção Configurações da interface, para que o relatório
possa comparar o medido com o vendido.

## A interface

A janela mostra:

- o estado do monitor (verde rodando, vermelho parado), com botões para parar,
  iniciar e disparar um teste de velocidade imediato;
- o painel "Agora": última latência e perda até a internet e até o roteador,
  última velocidade de download e upload e a disponibilidade das últimas 24 h;
- o resumo dos últimos 7 dias com a conclusão do diagnóstico e botões para
  abrir o relatório completo (7 ou 30 dias), exportar CSV e abrir a pasta de
  dados;
- as configurações que importam: velocidade do plano, frequência e tamanho do
  teste de velocidade (com o consumo diário estimado), horários sem teste de
  velocidade e a opção de iniciar junto com o Windows;
- as últimas linhas do registro.

A aba **Histórico** mostra o gráfico de todas as variáveis medidas, cada uma
em um painel com escala vertical própria e automática, ajustada ao que está
visível: download, upload, latência, jitter, perda de pacotes, perda até o
roteador, tempo de DNS e disponibilidade por dia. Marque e desmarque as
variáveis que quer ver. A janela de tempo vai de 1 a 15 dias (até 30 com a
roda do mouse) e pode ser navegada com os botões, arrastando o gráfico ou com
a roda do mouse para aproximar. "Agora" volta ao presente e o gráfico passa a
se atualizar sozinho a cada minuto. Passe o mouse sobre o gráfico para ler o
valor e o horário de cada ponto.

**Modo chamada.** A medição normal é uma vez por minuto, o que não enxerga
um travamento de 3 segundos. O modo chamada dispara 1 pacote por segundo para
a internet e para o roteador e registra um resumo por minuto e uma linha por
rajada de perda (início e duração), que é exatamente o que um congelamento de
vídeo tem por trás. Custa cerca de 0,5 MB por hora de banda e uns 5 KB de
disco. Ligue pelo botão "Ligar modo chamada" ou deixe automático em
Configurações, por exemplo `seg-sex 08:00-18:00`. Enquanto ele está ativo os
testes de velocidade ficam suspensos por padrão, porque o upload de teste
satura a conexão por cerca de um segundo e pode ele mesmo travar uma chamada
em outro computador da casa. As rajadas aparecem no gráfico de histórico como
"Travamentos", no relatório e no resumo.

**Marcar um travamento.** Com a janela do netmon em primeiro plano, aperte a
barra de espaço no momento em que a reunião travar. A seção "Travamentos que
você marcou", no Painel, tem o mesmo botão, conta as marcações do dia e lista
as últimas com o veredito de cada uma. A marcação é gravada com hora exata e
aparece como linha vertical no gráfico de histórico. Para conferir pela linha
de comando, `python netmon.py marks`. No relatório e no resumo, cada marcação vem
com o que o monitor viu naquele instante: uma rajada de perda no ping por
segundo (e se o roteador também falhou, o que aponta para a rede local), perda
ou latência na medição por minuto, ou nada anormal, caso em que o travamento
veio de outro lugar (o computador da chamada, o Wi-Fi dele, a VPN ou o serviço
de reunião). A tecla não interfere ao digitar nos campos de configuração.

Se o computador que monitora não é o mesmo das chamadas, ligue-o à rede do
mesmo jeito (Wi-Fi ou cabo, e na mesma banda de Wi-Fi) para que a medição até
o roteador represente o caminho que a chamada usa.

O histórico é mantido por tempo indeterminado (cerca de 300 MB por ano com os
padrões). Para limitar, defina `retention_days` no `config.json`; zero mantém
tudo.

**Atualizações**: ao abrir, a interface consulta a última release do
repositório. Se houver versão mais nova, aparece o botão "Atualizar para X",
que baixa e instala sozinho, reinicia o monitor e reabre a janela. No Windows
instalado pelo `netmon-setup.exe`, o instalador roda em modo silencioso; nas
instalações a partir do código, os arquivos são substituídos pela versão da
release. Para desligar a verificação, `update.check_on_start: false`.

Fechar a janela não interrompe a coleta: o monitor é um processo separado, sem
janela, que continua rodando. Ao abrir a interface de novo, se o monitor
estiver parado ela o inicia. Salvar as configurações reinicia o monitor para
aplicá-las.

## Instalação a partir do código (macOS, Linux ou quem preferir)

Requisito: Python 3.9 ou mais novo com Tkinter (o instalador de python.org já
inclui; no Linux, `sudo apt install python3-tk`). Nenhum `pip install` é
necessário.

```
cd netmon
python netmon_gui.py          # interface (inicia o monitor sozinha)
python netmon.py once         # ou, sem interface: valida a instalação
python netmon.py start        # inicia o monitor em segundo plano
python netmon.py status       # confere se está rodando
python netmon.py stop         # para
python netmon.py call on      # liga o modo chamada (call off desliga)
python netmon.py hops         # mostra os saltos da rede local que serão medidos
python netmon.py repair       # remarca artefatos de suspensão no histórico
```

Rodando a partir do código, `config.json`, banco e log ficam ao lado do
script. No executável instalado ficam em `%LOCALAPPDATA%\netmon` (Windows),
`~/Library/Application Support/netmon` (macOS) ou `~/.local/share/netmon`
(Linux). A variável de ambiente `NETMON_HOME` sobrepõe isso.

No `config.json`, além do que a interface expõe, pode interessar:

- `ping.gateway`: deixe `"auto"`; se a detecção falhar, informe o IP do roteador
  (em geral 192.168.0.1 ou 192.168.1.1).

## Deixar rodando em segundo plano

**Windows**: a caixa "Iniciar o monitor junto com o Windows" na interface cria
um atalho na pasta Inicializar do usuário (sem administrador). Os instaladores
já deixam isso ligado. Alternativa por tarefa agendada, que também reinicia o
processo se ele cair: `deploy/install-windows-task.ps1`.

**macOS**: edite `deploy/com.caioperet.netmon.plist` (usuário e caminho), copie
para `~/Library/LaunchAgents/` e carregue com `launchctl load -w`. As instruções
estão no próprio arquivo.

**Linux**: `deploy/netmon.service` é uma unidade systemd de usuário. As
instruções estão no próprio arquivo.

Em qualquer sistema o log fica em `netmon.log` na pasta de dados, com rotação
automática.

## Gerar o instalador Windows

O workflow `.github/workflows/netmon-windows.yml` roda a cada push que toque a
pasta `netmon` e publica o artefato `netmon-setup.exe`; um push de tag
`netmon-v*` também cria uma Release com o arquivo. Para gerar localmente em um
PC Windows com Python e Inno Setup 6 instalados:

```
powershell -ExecutionPolicy Bypass -File .\deploy\build-windows.ps1
```

O executável é gerado com PyInstaller (pasta `dist\netmon`) e empacotado por
`deploy/netmon.iss`.

## Ler os resultados

Pela interface: botões "Abrir relatório" e "Exportar CSV". Pela linha de
comando:

```
python netmon.py summary --days 7          # resumo em texto no terminal
python netmon.py report --days 7 --open    # relatório HTML, abre no navegador
python netmon.py export --table speed --out velocidade.csv
```

O relatório traz indicadores (disponibilidade, perda, latência, velocidade em
percentis, DNS, perda até o roteador), gráficos ao longo do tempo e por hora do
dia, a lista de quedas, os piores momentos e um diagnóstico em linguagem
direta. Ele pode ser anexado a uma reclamação formal ao provedor.

## Como interpretar

Deixe rodar pelo menos uma semana inteira, incluindo dias úteis e fim de
semana, antes de decidir. Os limiares usados no diagnóstico ficam em
`THRESHOLDS`, no início de `netmon.py`, e podem ser ajustados.

| Sinal | Leitura | Ação |
|---|---|---|
| Perda até o roteador acima de 1%, ou frequente | Problema local | Testar por cabo, trocar canal ou posição do Wi-Fi, trocar roteador. Trocar de provedor não resolve. |
| Roteador limpo, mas mais de 5% dos ciclos com perda >= 2% para a internet | Instabilidade do provedor | Reclamação formal com o relatório; se persistir, trocar de provedor ou de tecnologia. Upgrade não corrige perda. |
| Mais de 3 quedas ou 30 min fora do ar por semana | Instabilidade do provedor | Idem. |
| Latência p95 acima de 100 ms ou jitter p95 acima de 30 ms | Ruim para chamadas e VPN | Se o roteador está limpo, é do provedor. Verificar também bufferbloat (latência sobe quando alguém baixa algo). |
| Download p5 abaixo de 50% do plano, ou mediana abaixo de 70% | Provedor não entrega o contratado | Exigir a velocidade do plano antes de pagar por um maior. |
| Tudo estável e perto do plano, mas ainda falta | Plano pequeno para o uso | Aí sim, upgrade. |
| Perda e queda de velocidade concentradas das 19h às 23h | Congestionamento da rede do provedor | Típico de cabo coaxial e rádio; fibra costuma resolver. |

## Configuração completa

Veja `config.example.json`. Todas as chaves são opcionais; o que faltar assume o
padrão. Chaves relevantes:

- `ping.hosts`: destinos na internet. Aceita `host:porta` para o modo TCP.
- `ping.method`: `auto`, `icmp` ou `tcp`.
- `ping.count`, `ping.packet_interval_ms`, `ping.timeout_s`: pacotes por ciclo,
  intervalo entre eles e tempo máximo de espera.
- `speed.download_urls` e `speed.upload_urls`: listas tentadas em ordem; o
  marcador `{bytes}` é substituído pelo tamanho. Um servidor alternativo de
  download pode ser qualquer arquivo grande em HTTPS.
- `speed.max_seconds`: limite de tempo do download; encerra antes de completar
  os bytes em conexões lentas.
- `log_level`: `DEBUG` mostra cada ping no log.

## Estrutura dos dados

Banco SQLite `netmon.db` com as tabelas `ping` (uma linha por host por
ciclo), `dns`, `speed` (uma linha por direção por teste, inclusive falhas),
`call_minute` e `call_burst` (modo chamada: resumo por minuto e rajadas de
perda) e `events` (início e parada do monitor, quedas, modo chamada, testes
adiados e o motivo). Todos
os horários são timestamps Unix; o CSV exportado inclui a data legível.

Arquivos de controle na pasta de dados: `netmon.pid` (heartbeat do monitor, é
como a interface sabe que ele está vivo), `netmon.stop` (pedido de parada) e
`netmon.testnow` (pedido de teste imediato).
