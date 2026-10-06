export const meta = {
  name: 'laboratorio-modelos-de-negocio',
  description: 'Gera ideias, pesquisa, critica adversarialmente, avalia escala e margem com rigor crescente e ranqueia novos modelos de negócio para uma torreira',
  whenToUse: 'Explorar e ranquear modelos de negócio capazes de substituir o sharing de torres como motor de crescimento em 5 anos',
  phases: [
    { title: 'Geração', detail: '6 geradores independentes, cada um com uma lente' },
    { title: 'Seleção', detail: 'consolidação e seleção tolerante pelo Diretor' },
    { title: 'Pesquisa preliminar', detail: 'fatos básicos de cada ideia e Guardião em modo exploratório' },
    { title: 'Desenvolvimento', detail: '8 pares propositor x crítico por especialidade' },
    { title: 'Portão conceitual', detail: 'pre-mortem, painel do Guardião e decisão do Diretor' },
    { title: 'Pesquisa', detail: 'hipóteses críticas testadas contra dados reais e verificadas' },
    { title: 'Comitê final', detail: 'ranking, visão de portfólio e relatório' },
  ],
}

// ---------------------------------------------------------------- configuração
const CFG = Object.assign({
  base: '/home/user/Claude-Skills/business-model-lab',
  ideias_por_gerador: 4,
  max_finalistas: 4,
  rodadas_por_par: 2,
  max_retornos: 1,
  max_hipoteses_pesquisa: 5,
  limiar_conceitual: { nota: 3.3, prob: 0.30 },
  limiar_final: { nota: 3.7, prob: 0.40 },
  foco: '',
}, args || {})
const B = CFG.base

const LENTES = ['padroes', 'oceano_azul', 'jtbd', 'ativos', 'analogias', 'aquisicoes']
const PARES = {
  cliente: 'Cliente e trabalho a ser feito',
  tecnologia: 'Tecnologia e rede',
  ecossistema: 'Ecossistema e parcerias',
  regulatorio: 'Regulação e jurídico',
  estrategia: 'Estratégia competitiva e disrupção',
  economia: 'Economia unitária e margem',
  capital: 'Capital, caixa e financiamento',
  execucao: 'Execução e organização',
}
// Ordem de complementação: cliente primeiro; tecnologia, ecossistema e regulatório em paralelo;
// depois estratégia, economia (com checkpoint do Guardião), capital e execução.
const ETAPAS = [['cliente'], ['tecnologia', 'ecossistema', 'regulatorio'], ['estrategia'], ['economia'], ['capital'], ['execucao']]
const PESOS = { escala: 0.2, margem_caixa: 0.2, dor_cliente: 0.15, vantagem_propria: 0.15, viabilidade_tecnica_ecossistema: 0.1, defensabilidade: 0.1, execucao: 0.05, regulatorio: 0.05 }

const FOCO = CFG.foco ? ' Foco adicional definido pelo usuário: ' + CFG.foco : ''
// Os geradores leem só o brief, sem o contexto de mercado, para não se ancorarem nos mesmos temas.
const CAB_GER = `Você faz parte do Laboratório de Modelos de Negócio. Antes de tudo, leia ${B}/config/brief.md (missão, escada de degraus, lições das torres, ativos, taxas base e restrições). Não leia o contexto de mercado. Responda em português.${FOCO}`
const CAB = `Você faz parte do Laboratório de Modelos de Negócio. Antes de tudo, leia ${B}/config/brief.md (missão, escada de degraus, lições das torres, ativos, taxas base e restrições) e ${B}/config/contexto-mercado.md. Responda em português.${FOCO}`

// ---------------------------------------------------------------- schemas
const S = (properties, required) => ({ type: 'object', properties, required: required || Object.keys(properties) })
const str = { type: 'string' }
const num = { type: 'number' }
const strs = { type: 'array', items: str }
const faixa = S({ p10: num, p50: num, p90: num })

const IDEIA = S({ titulo: str, tese: str, cliente_pagador: str, job_to_be_done: str, ativos_alavancados: str, mecanismo_receita: str, mecanismo_margem: str, padroes: strs })
const IDEIAS = S({ ideias: { type: 'array', items: IDEIA } })
const CONSOLIDADO = S({ ideias: { type: 'array', items: S({ id: str, titulo: str, tese: str, cliente_pagador: str, job_to_be_done: str, ativos_alavancados: str, mecanismo_receita: str, mecanismo_margem: str, origens: strs }) } })
const SELECAO = S({
  selecionadas: { type: 'array', items: S({ id: str, titulo: str, tese: str, cliente_pagador: str, ativos_alavancados: str, mecanismo_receita: str, mecanismo_margem: str, ids_fundidos: strs, motivo: str }) },
  excluidas: { type: 'array', items: S({ id: str, titulo: str, motivo: str }) },
})
const PRELIM = S({
  respostas: { type: 'array', items: S({ pergunta: str, resposta: str, achados: { type: 'array', items: S({ dado: str, fonte_url: str, confiabilidade: { type: 'string', enum: ['alta', 'media', 'baixa'] } }) } }) },
  surpresas: strs,
  implicacoes_para_o_modelo: str,
})

const HIPOTESE = S({
  id: str, enunciado: str,
  tipo: { type: 'string', enum: ['fato', 'estimativa', 'premissa'] },
  probabilidade: num,
  criticidade: { type: 'string', enum: ['critica', 'alta', 'media', 'baixa'] },
  evidencia_necessaria: str, criterio_de_abandono: str,
})
const MODULO = S({
  resumo: str, analise: str,
  contribuicao_meta_dupla: str,
  hipoteses: { type: 'array', items: HIPOTESE },
  numeros_chave: { type: 'array', items: S({ nome: str, valor: str, base: { type: 'string', enum: ['fato', 'estimativa'] } }) },
  respostas_objecoes: { type: 'array', items: S({ objecao_id: str, resposta: { type: 'string', enum: ['aceita', 'ajustada', 'rebatida'] }, argumento: str }) },
  confianca: num,
}, ['resumo', 'analise', 'contribuicao_meta_dupla', 'hipoteses', 'numeros_chave', 'confianca'])
const CRITICA = S({
  versao_mais_forte: str,
  objecoes: { type: 'array', items: S({ id: str, severidade: { type: 'string', enum: ['fatal', 'alta', 'media', 'baixa'] }, hipotese_alvo: str, objecao: str, contra_proposta: str, evidencia_que_resolveria: str }) },
  objecoes_anteriores_resolvidas: strs,
  avaliacao_geral: str,
})
const PAINEL = S({
  receita_ano5: faixa, margem_ano5: faixa,
  teto_margem_estrutural: num, margem_incremental: num, conversao_caixa: num,
  roic: str, prob_meta_dupla: num, lacuna_escala: str, desvio_otimismo: str,
  premissas_que_mais_pesam: strs, alertas_margem: strs,
  degraus: S({ ano3: str, ano5: str, ano8: str, ano12: str }),
  melhorias_legitimas: str,
  alavancas: { type: 'array', items: S({ alavanca: str, efeito_receita: str, efeito_margem: str, como_testar: str, incorporada: { type: 'boolean' } }) },
  parecer: { type: 'string', enum: ['avanca', 'redesenha', 'complementar'] },
})
const PREMORTEM = S({ causas: { type: 'array', items: S({ causa: str, tipo: str, probabilidade: num, sinal_antecipado: str, mitigacao: str }) }, causa_mais_provavel: str })
const NOTAS = S(Object.fromEntries(Object.keys(PESOS).map(k => [k, num])))
const GATE = S({
  notas: NOTAS, prob_sucesso: num, taxa_base_usada: str,
  objecoes_fatais_abertas: strs,
  decisao: { type: 'string', enum: ['aprovar_pesquisa', 'devolver', 'arquivar'] },
  pares_a_revisar: { type: 'array', items: { type: 'string', enum: Object.keys(PARES) } },
  instrucoes: str,
  hipoteses_criticas: { type: 'array', items: S({ id: str, enunciado: str, porque_critica: str }) },
  justificativa: str,
})
const EVIDENCIA = S({
  achados: { type: 'array', items: S({ dado: str, fonte_url: str, tipo_fonte: str, data: str, confiabilidade: { type: 'string', enum: ['alta', 'media', 'baixa'] }, direcao: { type: 'string', enum: ['a_favor', 'contra', 'neutro'] } }) },
  estimativa_atualizada: str,
  veredito: { type: 'string', enum: ['sustentada', 'refutada', 'inconclusiva'] },
  comentario: str,
})
const VERIFICACAO = S({
  fontes_conferidas: { type: 'array', items: S({ fonte_url: str, confirma: { type: 'boolean' }, observacao: str }) },
  evidencia_contraria: { type: 'array', items: S({ dado: str, fonte_url: str }) },
  veredito_final: { type: 'string', enum: ['sustentada', 'refutada', 'inconclusiva'] },
  confianca: num,
})
const FINAL = S({
  notas: NOTAS, prob_sucesso: num, taxa_base_usada: str,
  decisao: { type: 'string', enum: ['recomendar', 'recomendar_com_condicoes', 'reprovar'] },
  tese_final: str, por_que_pode_dar_certo: str, principais_riscos: strs, condicoes: strs,
  experimentos: { type: 'array', items: S({ experimento: str, pergunta: str, criterio_sucesso: str, custo_prazo: str }) },
  criterios_de_abandono: strs, fontes_chave: strs,
})
const RANKING = S({
  ranking: { type: 'array', items: S({ posicao: num, id: str, titulo: str, decisao: str, nota_ponderada: num, prob_sucesso: num, embasamento: str }) },
  visao_portfolio: str,
  relatorio_markdown: str,
})

// ---------------------------------------------------------------- utilidades
const ponderar = n => n ? Math.round(Object.keys(PESOS).reduce((s, k) => s + (Number(n[k]) || 0) * PESOS[k], 0) * 100) / 100 : 0
const enxuto = p => JSON.stringify({
  ideia: p.ideia,
  modulos: p.modulos,
  objecoes_abertas: p.objecoes_abertas,
  pesquisa_preliminar: p.preliminar || null,
  ultimo_painel: p.paineis[p.paineis.length - 1] || null,
  premortem: p.premortem || null,
})

const FASE_MODO = { exploratorio: 'Pesquisa preliminar', desenvolvimento: 'Desenvolvimento', portao: 'Portão conceitual', rigoroso: 'Pesquisa' }
async function guardiao(p, momento) {
  const painel = await agent(`${CAB}
Seu papel: GUARDIÃO DE ESCALA E MARGEM. Leia ${B}/agents/guardiao.md.
Seu modo agora é "${momento}". Siga as regras desse modo. Produza o painel deste pacote.
Histórico dos seus painéis anteriores para este pacote (para detectar desvio de otimismo):
${JSON.stringify(p.paineis)}
Pacote:
${enxuto(p)}
${p.evidencias ? 'Evidências pesquisadas e verificadas (use-as como base principal):\n' + JSON.stringify(p.evidencias) : ''}`,
    { label: `${p.ideia.id}:guardião:${momento}`, phase: FASE_MODO[momento], schema: PAINEL })
  if (painel) p.paineis.push({ momento, ...painel })
  return painel
}

async function par(chave, p, instrucao) {
  const nome = PARES[chave]
  const id = p.ideia.id
  let mod = await agent(`${CAB}
Seu papel: PROPOSITOR do par "${nome}". Leia ${B}/agents/pares/${chave}.md (seção Propositor) e ${B}/agents/protocolo-propositor.md.
Use os ids de hipótese com o prefixo ${chave.slice(0, 3).toUpperCase()}-.
Pacote em desenvolvimento (ideia e módulos já produzidos pelos outros pares):
${enxuto(p)}
${instrucao ? 'INSTRUÇÕES DO DIRETOR PARA ESTA REVISÃO: ' + instrucao : ''}
${p.modulos[chave] ? 'Sua versão anterior deste módulo: ' + JSON.stringify(p.modulos[chave]) : ''}
Produza o seu módulo.`, { label: `${id}:${chave}:propõe`, phase: 'Desenvolvimento', schema: MODULO })
  if (!mod) return
  let abertas = []
  for (let r = 1; r <= CFG.rodadas_por_par; r++) {
    const { confianca, ...semConfianca } = mod
    const crit = await agent(`${CAB}
Seu papel: CRÍTICO ADVERSÁRIO do par "${nome}". Leia ${B}/agents/pares/${chave}.md (seção Crítico) e ${B}/agents/protocolo-critico.md.
Rodada ${r} de ${CFG.rodadas_por_par}.
Módulo a criticar:
${JSON.stringify(semConfianca)}
Contexto (ideia e outros módulos):
${enxuto(p)}
${abertas.length ? 'Suas objeções da rodada anterior (confira se foram resolvidas de verdade): ' + JSON.stringify(abertas) : ''}`,
      { label: `${id}:${chave}:critica-${r}`, phase: 'Desenvolvimento', schema: CRITICA })
    if (!crit) break
    abertas = crit.objecoes.filter(o => o.severidade !== 'baixa')
    const graves = abertas.filter(o => o.severidade === 'fatal' || o.severidade === 'alta')
    if (!graves.length || r === CFG.rodadas_por_par) break
    const rev = await agent(`${CAB}
Seu papel: PROPOSITOR do par "${nome}". Leia ${B}/agents/pares/${chave}.md e ${B}/agents/protocolo-propositor.md.
O crítico fez as objeções abaixo. Responda a CADA uma (aceita, ajustada ou rebatida com argumento) e entregue o módulo revisado.
Objeções: ${JSON.stringify(crit.objecoes)}
Seu módulo atual: ${JSON.stringify(mod)}
Contexto: ${enxuto(p)}`, { label: `${id}:${chave}:revisa-${r}`, phase: 'Desenvolvimento', schema: MODULO })
    if (rev) mod = rev
  }
  p.modulos[chave] = mod
  p.objecoes_abertas = p.objecoes_abertas.filter(o => o.par !== chave).concat(abertas.map(o => ({ ...o, par: chave })))
}

function aprovadoConceito(g, painel, p) {
  if (!g || g.decisao !== 'aprovar_pesquisa') return false
  const nota = ponderar(g.notas)
  const fatais = p.objecoes_abertas.filter(o => o.severidade === 'fatal').length + (g.objecoes_fatais_abertas || []).length
  const ok = nota >= CFG.limiar_conceitual.nota && g.prob_sucesso >= CFG.limiar_conceitual.prob && fatais === 0 && painel && painel.parecer !== 'complementar'
  if (!ok) log(`${p.ideia.id}: Diretor aprovou, mas o portão bloqueou (nota ${nota}, prob ${g.prob_sucesso}, fatais ${fatais}, parecer ${painel && painel.parecer}).`)
  return ok
}

// ---------------------------------------------------------------- 1. geração
phase('Geração')
const geradas = await parallel(LENTES.map(l => () => agent(`${CAB_GER}
Seu papel: GERADOR DIVERGENTE com a lente "${l}". Leia ${B}/agents/geradores.md (regras comuns e a seção da lente ${l}).
Trabalhe de forma independente. Gere ${CFG.ideias_por_gerador} ideias de modelo de negócio.`,
  { label: `gerador:${l}`, phase: 'Geração', schema: IDEIAS })))
const brutas = geradas.flatMap((g, i) => g ? g.ideias.map(x => ({ ...x, lente: LENTES[i] })) : [])
log(`${brutas.length} ideias brutas geradas por ${geradas.filter(Boolean).length} lentes.`)

// ---------------------------------------------------------------- 2. seleção tolerante
phase('Seleção')
const cons = await agent(`${CAB}
Consolide as ideias abaixo, vindas de geradores independentes. Junte as duplicadas ou muito parecidas (mantendo a melhor formulação e registrando as lentes de origem), preserve as distintas e dê a cada uma um id curto (I01, I02...). Não descarte nenhuma ideia distinta e não avalie mérito.
${JSON.stringify(brutas)}`, { label: 'consolidador', phase: 'Seleção', schema: CONSOLIDADO })
const candidatas = cons ? cons.ideias : []
const sel = await agent(`${CAB}
Seu papel: DIRETOR, na seleção inicial. Leia ${B}/agents/diretor.md (seção Na seleção inicial).
Selecione até ${CFG.max_finalistas} candidatas para a pesquisa preliminar. Postura tolerante: exclua só o que viola as restrições do brief; priorize potencial de subir os degraus e diversidade de temas.
Candidatas: ${JSON.stringify(candidatas)}`, { label: 'diretor:seleção', phase: 'Seleção', schema: SELECAO })
const finalistas = sel ? sel.selecionadas.slice(0, CFG.max_finalistas) : []
const arquivadas = sel ? sel.excluidas.map(x => ({ ...x, etapa: 'seleção' })) : []
if (sel && sel.selecionadas.length > finalistas.length) sel.selecionadas.slice(finalistas.length).forEach(x => arquivadas.push({ id: x.id, titulo: x.titulo, etapa: 'seleção', motivo: `Fora do limite de ${CFG.max_finalistas} finalistas.` }))
log(`Seleção: ${brutas.length} ideias brutas, ${candidatas.length} consolidadas, ${finalistas.length} seguem para pesquisa preliminar, ${arquivadas.length} ficaram de fora.`)

// ---------------------------------------------------------------- 3 a 5. desenvolvimento, portão, pesquisa
const resultados = await pipeline(finalistas,
  // Pesquisa preliminar e Guardião em modo exploratório (não elimina nada)
  async ideia => {
    const p = { ideia, modulos: {}, objecoes_abertas: [], paineis: [], decisoes: [] }
    p.preliminar = await agent(`${CAB}
Seu papel: PESQUISADOR DE EVIDÊNCIAS, em modo preliminar. Leia ${B}/agents/pesquisador.md. Use WebSearch e WebFetch (carregue via ToolSearch).
Ideia: ${JSON.stringify(ideia)}
Responda, com fontes, a estas perguntas:
1. Qual o tamanho e o crescimento do mercado que esta ideia endereça?
2. Que margens EBITDA têm negócios análogos já existentes, e que fatores explicam as diferenças?
3. Que evidência existe de demanda e de disposição a pagar dos clientes-alvo?
4. Quem já tentou algo parecido, e o que aconteceu?
5. Existe algum ativo hoje cativo que poderia ser adquirido para acelerar a escala?`, { label: `${ideia.id}:pesquisa-preliminar`, phase: 'Pesquisa preliminar', schema: PRELIM })
    await guardiao(p, 'exploratorio')
    return p
  },
  // Desenvolvimento por pares e portão conceitual
  async p => {
    const ideia = p.ideia
    for (const etapa of ETAPAS) {
      await parallel(etapa.map(k => () => par(k, p)))
      if (etapa[0] === 'economia') await guardiao(p, 'desenvolvimento')
    }
    let aprovado = false
    for (let t = 0; t <= CFG.max_retornos; t++) {
      p.premortem = await agent(`${CAB}
Seu papel: PRE-MORTEM. Leia ${B}/agents/premortem.md.
Pacote: ${enxuto(p)}`, { label: `${ideia.id}:pre-mortem`, phase: 'Portão conceitual', schema: PREMORTEM })
      const painel = await guardiao(p, 'portao')
      const g = await agent(`${CAB}
Seu papel: DIRETOR, no portão conceitual. Leia ${B}/agents/diretor.md e ${B}/config/rubrica.md.
${t === CFG.max_retornos ? 'Esta é a última avaliação permitida: escolha entre aprovar_pesquisa e arquivar.' : ''}
Pacote completo, com objeções abertas, painel do Guardião e pre-mortem:
${enxuto(p)}
Decisões anteriores do Diretor para este pacote: ${JSON.stringify(p.decisoes)}`,
        { label: `${ideia.id}:diretor:portão-${t + 1}`, phase: 'Portão conceitual', schema: GATE })
      if (!g) break
      p.decisoes.push({ ...g, nota_ponderada: ponderar(g.notas) })
      if (aprovadoConceito(g, painel, p)) { aprovado = true; break }
      if (g.decisao === 'aprovar_pesquisa') {
        // O portão bloqueou uma aprovação: o pacote volta para os pares com objeções graves abertas.
        const graves = p.objecoes_abertas.filter(o => o.severidade === 'fatal' || o.severidade === 'alta')
        g.decisao = 'devolver'
        g.pares_a_revisar = [...new Set(graves.map(o => o.par).concat(g.pares_a_revisar || []))]
        g.instrucoes = `Resolver com substância as objeções graves ainda abertas: ${JSON.stringify(graves)}. ${g.instrucoes || ''}`
        if (!g.pares_a_revisar.length) break
      }
      if (g.decisao !== 'devolver' || t === CFG.max_retornos) break
      log(`${ideia.id}: devolvido para ${g.pares_a_revisar.join(', ')}.`)
      for (const etapa of ETAPAS) {
        const alvo = etapa.filter(k => g.pares_a_revisar.includes(k))
        if (alvo.length) await parallel(alvo.map(k => () => par(k, p, g.instrucoes)))
      }
    }
    return { p, aprovado }
  },
  // Pesquisa contra dados reais e portão final
  async ({ p, aprovado }) => {
    if (!aprovado) return { p, final: null }
    const g = p.decisoes[p.decisoes.length - 1]
    const hips = g.hipoteses_criticas.slice(0, CFG.max_hipoteses_pesquisa)
    if (g.hipoteses_criticas.length > hips.length) log(`${p.ideia.id}: ${g.hipoteses_criticas.length - hips.length} hipóteses críticas ficaram fora da pesquisa (limite ${CFG.max_hipoteses_pesquisa}).`)
    const ev = await parallel(hips.map(h => async () => {
      const e = await agent(`${CAB}
Seu papel: PESQUISADOR DE EVIDÊNCIAS. Leia ${B}/agents/pesquisador.md. Use WebSearch e WebFetch (carregue via ToolSearch).
Modelo de negócio: ${JSON.stringify(p.ideia)}
Hipótese a testar: ${JSON.stringify(h)}`, { label: `${p.ideia.id}:${h.id}:pesquisa`, phase: 'Pesquisa', schema: EVIDENCIA })
      if (!e) return null
      const v = await agent(`${CAB}
Seu papel: VERIFICADOR ADVERSÁRIO. Leia ${B}/agents/verificador.md. Use WebSearch e WebFetch (carregue via ToolSearch).
Hipótese: ${JSON.stringify(h)}
Trabalho do pesquisador: ${JSON.stringify(e)}`, { label: `${p.ideia.id}:${h.id}:verifica`, phase: 'Pesquisa', schema: VERIFICACAO })
      return { hipotese: h, evidencia: e, verificacao: v }
    }))
    p.evidencias = ev.filter(Boolean)
    const painel = await guardiao(p, 'rigoroso')
    const f = await agent(`${CAB}
Seu papel: DIRETOR, no portão final. Leia ${B}/agents/diretor.md e ${B}/config/rubrica.md.
Pacote: ${enxuto(p)}
Evidências pesquisadas e verificadas: ${JSON.stringify(p.evidencias)}
Painel do Guardião com base em evidência: ${JSON.stringify(painel)}`, { label: `${p.ideia.id}:diretor:final`, phase: 'Pesquisa', schema: FINAL })
    if (f) {
      f.nota_ponderada = ponderar(f.notas)
      const refutada = p.evidencias.some(x => (x.verificacao ? x.verificacao.veredito_final : x.evidencia.veredito) === 'refutada')
      const ok = f.nota_ponderada >= CFG.limiar_final.nota && f.prob_sucesso >= CFG.limiar_final.prob && !refutada && painel && painel.parecer === 'avanca'
      if (f.decisao !== 'reprovar' && !ok) {
        log(`${p.ideia.id}: portão final rebaixou para reprovar (nota ${f.nota_ponderada}, prob ${f.prob_sucesso}, hipótese refutada: ${refutada}).`)
        f.decisao_original = f.decisao
        f.decisao = 'reprovar'
      }
    }
    return { p, final: f }
  },
)

// ---------------------------------------------------------------- 6. comitê final
phase('Comitê final')
const validos = resultados.filter(Boolean)
validos.filter(r => !r.final).forEach(r => {
  const d = r.p.decisoes[r.p.decisoes.length - 1]
  arquivadas.push({ id: r.p.ideia.id, titulo: r.p.ideia.titulo, etapa: 'portão conceitual', motivo: d ? d.justificativa : 'sem decisão' })
})
const comite = await agent(`${CAB}
Seu papel: DIRETOR, no comitê final. Leia ${B}/agents/diretor.md e ${B}/config/rubrica.md.
Compare os pacotes que passaram pela pesquisa e produza o ranking (recomendados primeiro), a visão de portfólio e um relatório executivo completo em Markdown.
O relatório deve trazer, para cada ideia ranqueada: tese, por que pode dar certo, números-chave (receita e margem no ano 5 em P10/P50/P90 e avaliação de cada degrau da escada), como o modelo reproduz as lições das torres, evidências verificadas com links, riscos, experimentos e critérios de abandono. Inclua também uma seção curta sobre as ideias arquivadas e o motivo.
Pacotes avaliados:
${JSON.stringify(validos.filter(r => r.final).map(r => ({ id: r.p.ideia.id, titulo: r.p.ideia.titulo, tese: r.p.ideia.tese, final: r.final, painel: r.p.paineis[r.p.paineis.length - 1], evidencias: r.p.evidencias, pesquisa_preliminar: r.p.preliminar })))}
Ideias arquivadas: ${JSON.stringify(arquivadas)}`, { label: 'diretor:comitê', phase: 'Comitê final', schema: RANKING })

return {
  config: CFG,
  ranking: comite ? comite.ranking : [],
  visao_portfolio: comite ? comite.visao_portfolio : '',
  relatorio_markdown: comite ? comite.relatorio_markdown : '',
  arquivadas,
  pacotes: validos.map(r => ({ ...r.p, final: r.final })),
}
