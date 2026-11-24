# Segurança — versão em microsserviços

## Controles implementados

- Cada serviço de negócio valida o JWT localmente: assinatura HS256, emissor fixo, algoritmo permitido, validade e campos obrigatórios.
- O segredo compartilhado é lido de `JWT_SEGREDO` no ambiente dos contêineres. Ele deve ser aleatório, ter pelo menos 32 caracteres e nunca ser versionado.
- Login valida hash scrypt com sal aleatório e bloqueia temporariamente após cinco tentativas malsucedidas por e-mail/endereço.
- Autorização por perfil é aplicada no serviço responsável pelo recurso. Solicitantes só listam e consultam os próprios chamados; atribuição e resolução são ações da equipe.
- Chamadas de Chamados para Usuários e Inventário propagam `Authorization` e possuem timeout. Falhas de rede/timeout retornam HTTP 503.
- O Gateway é o único componente com porta publicada no host. Serviços, Redis e bancos permanecem na rede interna do Compose.
- IDs de outros domínios são armazenados como referências simples, sem FK entre bancos. O serviço de Chamados consulta a existência do usuário/equipamento pela API correspondente antes de operações que exigem essa validação.
- Os contêineres Python rodam como usuário sem privilégios, com filesystem somente leitura, capacidades removidas e `no-new-privileges`.
- A interface escapa texto vindo da API antes de inserir HTML e recebe cabeçalhos de segurança HTTP.

## Configuração obrigatória

Copie `.env.example` para `.env`, gere um segredo com `python -c "import secrets; print(secrets.token_urlsafe(48))"` e preencha `JWT_SEGREDO`. Não use o valor de teste nem envie o `.env` ao GitHub.

As contas de exemplo usam uma senha conhecida e servem apenas para demonstração. Em ambiente real, use `DADOS_EXEMPLO=0`, senhas individuais, HTTPS, gestão segura de segredos, backups e monitoramento.

## Limitações didáticas conhecidas

- O bloqueio de tentativas de login fica em memória do serviço de Usuários; em uma implantação com múltiplas réplicas deve ser movido para um armazenamento compartilhado, como Redis.
- O exemplo usa SQLite em volumes Docker para manter a demonstração simples. Um ambiente real pode usar instâncias PostgreSQL independentes por serviço.
- A validação dos tokens é local e não implementa revogação imediata. Para revogação/contas desativadas, adote lista de revogação ou introspecção controlada.
- A publicação de eventos é tolerante a falhas para preservar a abertura de chamados. Em produção, adote outbox transacional, retentativas e dead-letter queue para garantir entrega.
