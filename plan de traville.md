# **Cahier des Charges Détaillé : Application Web "SamaConcept \- GeoProjects"**

## **1\. Présentation du Projet & Objectifs**

### **1.1 Contexte**

**SamaConcept** est un bureau d'études spécialisé en ingénierie et architecture. Chaque projet géré possède un répertoire dédié sur serveur (ex: /data/projects/Nom\_Projet/) regroupant tous les documents associés : plans AutoCAD (.dwg), pièces écrites Word (.docx), descriptifs PDF, images du site, notes de calcul, etc.

### **1.2 Objectif**

Développer une application web **100% conteneurisée, légère et réactive**, permettant à l'équipe de :

1. Centraliser et cartographier les projets sur un globe 3D interactif (**Globe.GL**).  
2. Retrouver instantanément n'importe quel projet via un **moteur de recherche et filtrage multi-critères réactif**.  
3. Automatiser l'indexation des dossiers grâce à un **agent AI (compatible OpenAI)** qui extrait les métadonnées et génère un fichier project.yaml à la racine de chaque dossier projet.  
4. Conserver une architecture sans base de données lourde, en s'appuyant sur les fichiers project.yaml mis en cache dans une base SQLite locale.

   ## **2\. Architecture Technique & Stack**

| Composant | Technologie | Rôle & Justification |
| :---- | :---- | :---- |
| **Frontend** | HTML5 / Bootstrap 5 \+ Globe.GL | Interface responsive, tableau de bord réactif, rendu 3D interactif |
| **Backend** | Python 3.11 / FastAPI | API REST haute performance, exécution asynchrone des tâches de scan |
| **Extraction AI** | Client Python OpenAI-compatible | Extraction automatique des métadonnées vers project.yaml |
| **Base de Données** | SQLite | Cache de lecture rapide indexé, synchronisé depuis les project.yaml |
| **Déploiement** | Docker & Docker Compose | Conteneur unique englobant l'application pour un déploiement zéro-effort |

\+---------------------------------------------------------------------------------+  
|                                 DOCKER CONTAINER                                |  
|                                                                                 |  
|  \+------------------------+      \+-------------------------------------------+  |  
|  |   Frontend (Bootstrap) | \<--\> |           Backend (FastAPI)               |  |  
|  |    \+ Globe.GL (3D)     |      |  \- Endpoints API (Recherche/Filtres)      |  |  
|  \+------------------------+      |  \- Worker / Agent AI Sync                 |  |  
|                                  \+--------------------+----------------------+  |  
|                                                       |                         |  
|                                            \+----------v----------+              |  
|                                            | Cache SQLite        |              |  
|                                            \+---------------------+              |  
\+-------------------------------------------------------^-------------------------+  
                                                        |  
                                          \+-------------+-------------+  
                                          | Dossier Racine des Projets|  
                                          | \- Projet\_A/project.yaml   |  
                                          | \- Projet\_B/project.yaml   |  
                                          \+---------------------------+

## **3\. Structure des Fichiers & Formats de Données**

### **3.1 Arborescence du Répertoire Projets**

L'application pointe sur un dossier principal monté via Docker :

Plaintext  
/data/projects/  
├── PROJ\_2026\_001\_Tour\_Alpha/  
│   ├── project.yaml            \# Généré/mis à jour par l'agent AI  
│   ├── Plans\_Architecte.dwg  
│   ├── CPS\_Definitif.pdf  
│   └── Photos/  
├── PROJ\_2026\_002\_Résidence\_Oasis/  
│   ├── project.yaml  
│   └── Etude\_Impact.docx

### **3.2 Fichier de Configuration Global (config.yaml)**

Placé dans l'application backend pour paramétrer le système :

YAML  
app:  
  projects\_root\_dir: "/data/projects"  
  sync\_interval\_minutes: 15  
  host: "0.0.0.0"  
  port: 8000

llm\_agent:  
  base\_url: "https://api.openai.com/v1" \# Compatible OpenAI (GPT-4o, Ollama, VLLM, LM Studio)  
  api\_key: "sk-proj-xxxx"  
  model: "gpt-4o-mini"  
  temperature: 0.1

### **3.3 Fichier Métadonnées Projet (project.yaml)**

Fichier unique présent dans chaque dossier projet :

YAML  
id: "PROJ\_2026\_001"  
nom\_projet: "Résidence Tour Alpha"  
ref\_administrative: "PERMIS-BUILD-2026-88"  
promoteur: "Groupe Immobilier Horizon"  
adresse: "12 Avenue Hassan II, Tanger"  
coordonnees\_gps:  
  latitude: 35.7796  
  longitude: \-5.8037  
statut: "en\_cours" \# Valeurs strictes: devis | en\_cours | finalise | livre  
etape\_actuelle: "Étude de structure"  
derniere\_mise\_a\_jour: "2026-09-10T10:00:00Z"

## **4\. Traitement AI & Synchronisation SQLite**

### **4.1 Robot Agent AI**

1. Un worker d'arrière-plan surveille la racine /data/projects.  
2. Si un dossier projet n'a pas de project.yaml ou si ses fichiers ont été récents :  
   * L'agent AI analyse l'arborescence et lit les documents texte (PDF/Word/DWG metadata).  
   * L'agent extrait les informations (Coordonnées GPS, Nom, Promoteur, Statut, Réf Administrative).  
   * L'agent écrit/met à jour le fichier project.yaml dans le dossier concerné.  
   * *Fallback* : Si les coordonnées GPS ne sont pas trouvables, écrire latitude: 0.0 et longitude: 0.0.

     ### **4.2 Base SQLite (sqlite.db)**

     Chaque fichier project.yaml valide est lu par le serveur et mis en cache dans SQLite pour offrir des temps de réponse d'API de l'ordre de quelques millisecondes.

SQL  
CREATE TABLE IF NOT EXISTS projects (  
    id TEXT PRIMARY KEY,  
    folder\_name TEXT UNIQUE,  
    nom\_projet TEXT,  
    ref\_administrative TEXT,  
    promoteur TEXT,  
    adresse TEXT,  
    latitude REAL,  
    longitude REAL,  
    statut TEXT,  
    etape\_actuelle TEXT,  
    derniere\_mise\_a\_jour DATETIME  
);

\-- Index pour optimiser les filtres multi-critères  
CREATE INDEX IF NOT EXISTS idx\_projects\_statut ON projects(statut);  
CREATE INDEX IF NOT EXISTS idx\_projects\_promoteur ON projects(promoteur);  
CREATE INDEX IF NOT EXISTS idx\_projects\_coords ON projects(latitude, longitude);

## **5\. Moteur de Filtrage Multi-Critères (Cœur Fonctionnel)**

Le système de filtrage est le module central. Il doit fonctionner en mode **multi-critères simultanés** avec une logique **ET** entre les critères, et une logique **OU** au sein d'un même filtre à choix multiples.

### **5.1 Critères de Filtrage Disponibles**

| Critère | Interface Utilisateur (UI) | Fonctionnement |
| :---- | :---- | :---- |
| **Recherche globale** | Champ texte (input search) | Recherche textuelle partielle dans nom\_projet, ref\_administrative, adresse, promoteur. |
| **Statut du projet** | Checkboxes / Badges colorés | Sélection multiple parmi : devis, en\_cours, finalise, livre. |
| **Promoteur / Client** | Menu déroulant (select dynamique) | Alimenté dynamiquement par la liste des promoteurs uniques enregistrés en base. |
| **Étape actuelle** | Champ texte / Autocomplete | Recherche par mot-clé dans le champ etape\_actuelle. |
| **Filtre Géographique** | Interrupteur (toggle) | *"Projets géolocalisés uniquement"* (exclut les coordonnées 0.0, 0.0). |

### **5.2 Requête API Backend (FastAPI)**

Endpoint : GET /api/projects  
Exemple d'appel d'API généré par l'interface lors d'un filtrage :

HTTP  
GET /api/projects?q=Horizon\&statut=en\_cours\&statut=devis\&promoteur=Groupe+Horizon\&has\_gps=true

Squelette de la requête SQL générée dynamiquement :

SQL  
SELECT \* FROM projects   
WHERE 1\=1  
  AND (nom\_projet LIKE %q% OR ref\_administrative LIKE %q% OR adresse LIKE %q% OR promoteur LIKE %q%)  
  AND statut IN ('en\_cours', 'devis')  
  AND promoteur \= 'Groupe Horizon'  
  AND (latitude \!= 0.0 AND longitude \!= 0.0)  
ORDER BY derniere\_mise\_a\_jour DESC;

## **6\. Endpoints API REST (FastAPI)**

* **GET /api/projects** : Retourne la liste des projets filtrés selon les paramètres passés en Query Params.  
* **GET /api/projects/{id}** : Retourne la fiche complète d'un projet ainsi que la liste des fichiers contenus dans son dossier.  
* **GET /api/promoters** : Liste tous les promoteurs uniques (pour alimenter le filtre déroulant).  
* **POST /api/sync** : Force la resynchronisation immédiate des fichiers project.yaml vers SQLite.  
* **GET /api/stats** : Retourne le nombre total de projets groupés par statut.

  ## **7\. Interface Utilisateur & Interactions (Frontend)**

  ### **7.1 Page Unique (Dashboard Full-Screen)**

* **Panneau Latéral Gauche (1/3 écran)** :  
  * Zone de recherche globale et filtres multi-critères.  
  * Bouton "Réinitialiser les filtres".  
  * Compteur réactif (ex: *"12 projets trouvés"*).  
  * Liste déroulante des cartes de projet. Chaque carte affiche le nom, le statut (badge), le promoteur et la ville.  
* **Zone Principale Droite (2/3 écran)** :  
  * Globe 3D interactif alimenté par **Globe.GL**.  
  * Rendu des marqueurs selon leur statut :  
    * 🟡 **Jaune** : devis  
    * 🔵 **Bleu** : en\_cours  
    * 🟢 **Vert** : finalise / livre  
  * **Interactions** :  
    * Survol d'un marqueur : Infobulle avec Nom du projet, Promoteur et Étape.  
    * Clic sur un marqueur ou sur une carte latérale : Centrage/zoom fluide de la caméra 3D sur le projet et ouverture d'une fenêtre avec le détail du dossier.

      ## **8\. Déploiement Conteneurisé (Docker Compose)**

      L'application doit être livrée avec les fichiers de déploiement suivants :

      ### **Dockerfile**

Dockerfile  
FROM python:3.11\-slim  
WORKDIR /app  
COPY requirements.txt .  
RUN pip install \--no-cache-dir \-r requirements.txt  
COPY . .  
EXPOSE 8000  
CMD \["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"\]

### **docker-compose.yml**

YAML  
version: '3.8'

services:  
  samaconcept-geoprojects:  
    build: .  
    container\_name: samaconcept\_app  
    ports:  
      \- "8000:8000"  
    volumes:  
      \# Dossier principal contenant tous les répertoires de projets  
      \- /chemin/local/projets:/data/projects  
      \# Persistance de la BDD SQLite et de la configuration  
      \- ./data\_app:/app/data  
      \- ./config.yaml:/app/config.yaml:ro  
    restart: always  
    environment:  
      \- PYTHONUNBUFFERED=1

## **9\. Directives Importantes pour l'Agent Codeur**

1. **Robustesse du Parsing YAML** : Utiliser pydantic pour valider la structure des fichiers project.yaml. Si un fichier est malformé, ignorer l'enregistrement défectueux et consigner une erreur dans les logs sans interrompre l'application.  
2. **Réactivité UI (Debounce)** : Implémenter un *debounce* de 300 ms sur le champ de recherche textuelle frontend afin d'éviter de déclencher une requête HTTP à chaque frappe de touche.  
3. **Mise à jour dynamique de Globe.GL** : Lors de la réception de la liste filtrée depuis FastAPI, mettre à jour le tableau d'objets pointsData() de Globe.GL pour effacer instantanément les marqueurs masqués.  
4. **Indépendance** : Ne requérir aucune dépendance externe lourde en dehors de la clé API pour l'agent AI. Le cache SQLite garantit que l'application reste utilisable même en cas de coupure Internet.