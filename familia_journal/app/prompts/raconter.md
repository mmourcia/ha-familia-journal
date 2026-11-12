Passe « raconter ».

1. Lis `todo.json` : `groupes_a_ecrire` (un article par rubrique et par jour, avec tous les `elements` du groupe et l'éventuel
   `article_existant`), `isoles` et `isoles_a_resumer_en_breve` (éléments qui n'entrent dans aucun article), le
   `contexte_avant` et `articles_existants`. Lis `categories.json` et `famille.md` s'il existe.
2. Ouvre les photos et les documents dont tu parles (chemin `media`) pour les décrire fidèlement.
3. Pour chaque groupe de `groupes_a_ecrire`, écris l'article, ou complète l'article existant (même `id` = `article_id`).
4. Pour chaque `id` de `isoles_a_resumer_en_breve` qui mérite d'être mentionné, écris une brève de 20 mots au plus.
5. Écris `patch.json` au format de la passe « raconter » : `articles` et `breves`. Suis les règles de la section
   « Raconter » de `CLAUDE.md`.

Si rien ne s'y prête, écris `{"articles": [], "breves": {}}` dans `patch.json`.
