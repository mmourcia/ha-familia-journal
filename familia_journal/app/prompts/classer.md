Passe « classer ».

1. Lis `todo.json` (les éléments `nouveaux`, avec `contexte_avant`), `categories.json` et `famille.md` s'il existe.
2. Ouvre chaque nouvelle photo et chaque `poster` de nouvelle vidéo (chemins `media` et `poster`).
3. Regarde `articles_existants` : si une nouvelle photo ou vidéo illustre clairement l'un d'eux, rattache-la.
4. Marque dans `importants` (trois au plus) ce qui compte pour la une du jour.
5. Écris `patch.json` au format de la passe « classer » : `classification`, `reviewed`, `hold`, `captions`, `fit`,
   `rattachements`, `importants`. Ne rédige aucun article.

Si `nouveaux` est vide, écris `{}` dans `patch.json`.
