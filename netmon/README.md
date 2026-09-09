# netmon: monitor leve da conexão de internet

Um único arquivo Python, sem dependências externas, que roda em segundo plano e
registra a qualidade da sua conexão ao longo de dias ou semanas. O objetivo é
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
| Latência e perda até o roteador | a cada 60 s | mesmos pings para o gateway detectado automaticamente | desprezível |
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

Uma nota sobre os números de velocidade: transferências curtas ficam um pouco
abaixo de um teste completo do Speedtest, porque parte do tempo é gasta na
aceleração inicial do TCP. Para a decisão que interessa, o que vale é a
comparação entre horários e a variação ao longo dos dias, e para isso o método
é consistente. Se quiser valores absolutos mais próximos do teste completo,
aumente `download_bytes` e `upload_bytes` (e o custo de banda junto).

## Instalação

Requisito: Python 3.9 ou mais novo (`python --version`). Nenhum `pip install`
é necessário.

```
cd netmon
python netmon.py once
```

O comando `once` cria o `config.json` a partir dos padrões, detecta o roteador,
escolhe o método de ping e roda todas as medições uma vez, imprimindo o
resultado. Se tudo aparecer, edite o `config.json`:

- `plan.download_mbps` e `plan.upload_mbps`: a velocidade contratada. Sem isso o
  relatório não consegue dizer se o provedor entrega o que vende.
- `speed.quiet_hours`: horários das suas reuniões fixas, se houver.
- `ping.gateway`: deixe `"auto"`; se a detecção falhar, informe o IP do roteador
  (em geral 192.168.0.1 ou 192.168.1.1).

Para rodar continuamente em primeiro plano (útil para testar):

```
python netmon.py run
```

## Deixar rodando em segundo plano

**Windows**: no PowerShell, dentro da pasta `deploy`:

```
powershell -ExecutionPolicy Bypass -File .\install-windows-task.ps1
```

Isso registra uma tarefa agendada que inicia no logon, roda com `pythonw.exe`
(sem janela) e reinicia sozinha se cair. Para remover:
`Unregister-ScheduledTask -TaskName netmon -Confirm:$false`.

**macOS**: edite `deploy/com.caioperet.netmon.plist` (usuário e caminho), copie
para `~/Library/LaunchAgents/` e carregue com `launchctl load -w`. As instruções
estão no próprio arquivo.

**Linux**: `deploy/netmon.service` é uma unidade systemd de usuário. As
instruções estão no próprio arquivo.

Em qualquer sistema o log fica em `netmon.log` ao lado do script, com rotação
automática.

## Ler os resultados

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

Banco SQLite `netmon.db` com quatro tabelas: `ping` (uma linha por host por
ciclo), `dns`, `speed` (uma linha por direção por teste, inclusive falhas) e
`events` (início e parada do monitor, quedas, testes adiados e o motivo). Todos
os horários são timestamps Unix; o CSV exportado inclui a data legível.
