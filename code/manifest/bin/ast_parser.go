// code/manifest/bin/ast_parser.go
package main

import (
	"bufio"
	"bytes"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"io/ioutil"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	// "encoding/base64" // Retiré car sanitizeIdentifier n'utilise plus base64
)

// FragmentManifest est la structure racine du JSON de sortie.
type FragmentManifest struct {
	Fragments map[string]FragmentInfo `json:"fragments"`
}

// ImportInfo contient les détails d'une déclaration d'import.
type ImportInfo struct {
	Name string `json:"name,omitempty"` // Alias (ex: v pour viper)
	Path string `json:"path"`           // Chemin d'import (ex: "github.com/spf13/viper")
}

// FragmentInfo contient les métadonnées d'un fragment de code.
type FragmentInfo struct {
	OriginalPath     string       `json:"original_path"`      // Chemin du fichier .go (ex: main.go, admin_templ.go)
	ActualSourcePath string       `json:"actual_source_path"` // Chemin du .templ si applicable, sinon OriginalPath
	IsTemplSource    bool         `json:"is_templ_source"`    // True si ActualSourcePath est un .templ
	PackageName      string       `json:"package_name"`
	FragmentType     string       `json:"fragment_type"`           // "function", "method", "type", "constant", "variable"
	Identifier       string       `json:"identifier"`              // Nom func/methode/type/const/var
	ReceiverType     string       `json:"receiver_type,omitempty"` // Pour méthodes
	Signature        string       `json:"signature,omitempty"`     // Pour funcs/methods
	Definition       string       `json:"definition,omitempty"`    // Pour types, consts, vars
	Docstring        string       `json:"docstring,omitempty"`     // Docstring extrait de l'AST du .go
	StartLine        int          `json:"start_line"`              // Ligne de début dans OriginalPath
	EndLine          int          `json:"end_line"`                // Ligne de fin dans OriginalPath
	Imports          []ImportInfo `json:"imports,omitempty"`       // Imports du fichier OriginalPath
	CodeDigest       string       `json:"code_digest,omitempty"`   // SHA-1 du noeud formaté du fragment dans OriginalPath
	// Les champs suivants sont initialisés mais non remplis par ce parseur basique.
	// Ils pourraient être utilisés par des analyses plus poussées.
	DirectCallsInternal []string `json:"direct_calls_internal,omitempty"`
	TypesUsedInternal   []string `json:"types_used_internal,omitempty"`
}

// visitor pour parcourir l'AST
type visitor struct {
	fset                       *token.FileSet
	fragments                  map[string]FragmentInfo
	currentOriginalPathRel     string // Chemin relatif du fichier .go en cours d'analyse
	currentActualSourcePathRel string // Chemin relatif du .templ source si applicable
	currentIsTemplSource       bool   // True si on traite le source .templ
	currentPackageName         string
	currentFileImports         []ImportInfo
	projectRootDirAbs          string // Racine absolue du projet pour résoudre les chemins .templ
}

// --- Main Function ---
func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "Usage: %s <directory_path>\n", os.Args[0])
		os.Exit(1)
	}
	rootDir := os.Args[1]
	absRootDir, err := filepath.Abs(rootDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Erreur: Résolution chemin absolu pour %q échouée: %v\n", rootDir, err)
		os.Exit(1)
	}

	manifest := FragmentManifest{Fragments: make(map[string]FragmentInfo)}
	fset := token.NewFileSet()

	fmt.Fprintf(os.Stderr, "[AST Parser] Analyse du projet Go dans: %s\n", absRootDir)

	err = filepath.Walk(absRootDir, func(path string, fileinfo os.FileInfo, walkErr error) error {
		if walkErr != nil {
			fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Erreur accès à %q: %v\n", path, walkErr)
			return nil // Tenter de continuer
		}

		if fileinfo.IsDir() {
			dirName := fileinfo.Name()
			// Ignorer les dossiers connus et les dossiers cachés
			// Ajout de "webroot/static" ou "public" si ce sont des assets compilés
			if dirName == ".git" || dirName == "vendor" || dirName == "node_modules" ||
				dirName == "venv" || dirName == ".idea" || dirName == ".vscode" ||
				dirName == "tmp_go_format" || dirName == "static" || dirName == "public" || // Exclure les assets statiques courants
				strings.HasPrefix(dirName, ".") {
				fmt.Fprintf(os.Stderr, "[AST Parser] Ignoré dossier: %s\n", path)
				return filepath.SkipDir
			}
			return nil
		}

		lowerPath := strings.ToLower(path)
		// Ignorer les fichiers non-Go et les fichiers de test Go
		if !strings.HasSuffix(lowerPath, ".go") || strings.HasSuffix(lowerPath, "_test.go") {
			return nil
		}

		// originalGoPathRel est le chemin relatif du fichier .go traité
		originalGoPathRel, err := filepath.Rel(absRootDir, path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Échec calcul chemin relatif pour %q: %v. Utilisation chemin complet.\n", path, err)
			originalGoPathRel = path
		}
		originalGoPathRel = filepath.ToSlash(originalGoPathRel)

		fmt.Fprintf(os.Stderr, "[AST Parser] Parsing du fichier Go: %s\n", originalGoPathRel)
		contentBytes, err := ioutil.ReadFile(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Échec lecture fichier %q: %v\n", path, err)
			return nil
		}

		node, err := parser.ParseFile(fset, path, contentBytes, parser.ParseComments)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Échec parsing fichier %q: %v\n", originalGoPathRel, err)
			return nil
		}

		// Déterminer si c'est un fichier _templ.go et trouver son source .templ
		var actualSrcPathRel string
		var isTemplSrc bool
		if strings.HasSuffix(originalGoPathRel, "_templ.go") {
			// path est le chemin absolu du fichier _templ.go
			templSrc, found := findTemplSourcePath(path, absRootDir)
			if found {
				actualSrcPathRel = templSrc
				isTemplSrc = true
				fmt.Fprintf(os.Stderr, "[AST Parser]   -> Fichier source .templ identifié: %s\n", actualSrcPathRel)
			} else {
				actualSrcPathRel = originalGoPathRel // Fallback sur le _templ.go
				isTemplSrc = false
				fmt.Fprintf(os.Stderr, "[AST Parser]   -> Fichier source .templ non trouvé pour %s, utilisation de _templ.go lui-même.\n", originalGoPathRel)
			}
		} else {
			actualSrcPathRel = originalGoPathRel
			isTemplSrc = false
		}

		v := &visitor{
			fset:                       fset,
			fragments:                  manifest.Fragments,
			currentOriginalPathRel:     originalGoPathRel, // Toujours le .go
			currentActualSourcePathRel: actualSrcPathRel,  // Le .templ ou le .go
			currentIsTemplSource:       isTemplSrc,
			currentPackageName:         node.Name.Name,
			currentFileImports:         extractImports(node),
			projectRootDirAbs:          absRootDir,
		}

		ast.Walk(v, node)
		return nil
	})

	if err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Erreur fatale parcours répertoire %q: %v\n", rootDir, err)
		os.Exit(1)
	}

	fmt.Fprintf(os.Stderr, "[AST Parser] Fin parcours. %d fragments. Marshalling JSON...\n", len(manifest.Fragments))
	jsonData, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Erreur marshalling JSON: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(string(jsonData))
	fmt.Fprintf(os.Stderr, "[AST Parser] Analyse terminée. Manifeste JSON généré.\n")
}

// findTemplSourcePath tente de trouver le .templ source pour un _templ.go donné.
// goTemplFileAbsPath: chemin absolu du fichier _templ.go.
// projectRootDirAbs: chemin absolu de la racine du projet Go.
// Retourne: chemin relatif du .templ par rapport à projectRootDirAbs, bool indiquant si trouvé.
func findTemplSourcePath(goTemplFileAbsPath string, projectRootDirAbs string) (string, bool) {
	dir := filepath.Dir(goTemplFileAbsPath)
	baseName := filepath.Base(goTemplFileAbsPath)

	// 1. Essayer la convention de nommage: foo_templ.go -> foo.templ
	if strings.HasSuffix(baseName, "_templ.go") {
		templFileName := strings.TrimSuffix(baseName, "_templ.go") + ".templ"
		potentialTemplPathAbs := filepath.Join(dir, templFileName)
		if _, err := os.Stat(potentialTemplPathAbs); err == nil {
			relPath, errRel := filepath.Rel(projectRootDirAbs, potentialTemplPathAbs)
			if errRel == nil {
				return filepath.ToSlash(relPath), true
			}
			fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Convention .templ: erreur calcul rel path pour %s (base: %s): %v\n", potentialTemplPathAbs, projectRootDirAbs, errRel)
		}
	}

	// 2. Fallback sur le commentaire "// File: ..."
	file, err := os.Open(goTemplFileAbsPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Erreur ouverture %s pour commentaire .templ: %v\n", goTemplFileAbsPath, err)
		return "", false
	}
	defer file.Close()

	// Regex pour `// File: path/to/something.templ` (doit se terminer par .templ)
	re := regexp.MustCompile(`^\s*//\s*File:\s*(.+\.templ)\s*$`)
	scanner := bufio.NewScanner(file)
	for i := 0; i < 20 && scanner.Scan(); i++ { // Limiter la recherche aux premières lignes
		line := scanner.Text()
		matches := re.FindStringSubmatch(line)
		if len(matches) > 1 {
			pathFromComment := strings.TrimSpace(matches[1])
			// pathFromComment est relatif à la racine du projet où `templ generate` a été exécuté.
			// On suppose que c'est projectRootDirAbs.
			absPathFromComment := filepath.Join(projectRootDirAbs, pathFromComment)
			if _, err := os.Stat(absPathFromComment); err == nil {
				// S'assurer de retourner le chemin relatif au projet, pas celui du commentaire brut s'il est différent
				relPath, errRel := filepath.Rel(projectRootDirAbs, absPathFromComment)
				if errRel == nil {
					return filepath.ToSlash(relPath), true
				}
				fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Commentaire .templ: erreur calcul rel path pour %s (base: %s): %v\n", absPathFromComment, projectRootDirAbs, errRel)
			} else {
				fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Commentaire .templ trouvé: '%s', mais fichier inexistant à: '%s'\n", pathFromComment, absPathFromComment)
			}
			return "", false // Commentaire trouvé mais fichier invalide ou erreur chemin
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Erreur lecture %s pour commentaire .templ: %v\n", goTemplFileAbsPath, err)
	}
	return "", false // Non trouvé
}

// Méthode Visit de la structure visitor
func (v *visitor) Visit(node ast.Node) ast.Visitor {
	if node == nil {
		return nil
	}
	pos, endPos := node.Pos(), node.End()
	if !pos.IsValid() || !endPos.IsValid() {
		return v
	}

	fragmentID := ""
	// Initialiser FragmentInfo avec les données du fichier en cours
	info := FragmentInfo{
		OriginalPath:        v.currentOriginalPathRel,     // Chemin du .go (ex: foo_templ.go)
		ActualSourcePath:    v.currentActualSourcePathRel, // Chemin du .templ ou du .go
		IsTemplSource:       v.currentIsTemplSource,
		PackageName:         v.currentPackageName,
		StartLine:           v.fset.Position(pos).Line,    // Lignes relatives à OriginalPath
		EndLine:             v.fset.Position(endPos).Line, // Lignes relatives à OriginalPath
		Imports:             v.currentFileImports,
		DirectCallsInternal: []string{},
		TypesUsedInternal:   []string{},
	}

	switch x := node.(type) {
	case *ast.FuncDecl:
		if x.Name == nil || x.Name.Name == "_" || x.Name.Name == "init" { // Ignorer aussi les fonctions init
			return v
		}
		info.Identifier = x.Name.Name
		info.Docstring = getDocstring(x.Doc) // Docstring de l'AST du .go
		info.Signature = buildSignatureString(v.fset, x)

		// Construire un fragmentID basé sur OriginalPath pour l'unicité des fragments du .go
		// On utilise le nom du fichier .go sans extension pour la base de l'ID.
		goFileNameWithoutExt := strings.TrimSuffix(filepath.Base(v.currentOriginalPathRel), ".go")
		fragmentIDBase := fmt.Sprintf("%s_%s", v.currentPackageName, goFileNameWithoutExt)

		if x.Recv != nil && len(x.Recv.List) > 0 {
			info.FragmentType = "method"
			info.ReceiverType = typeToString(v.fset, x.Recv.List[0].Type)
			fragmentID = fmt.Sprintf("%s_%s_%s", fragmentIDBase, sanitizeIdentifier(info.ReceiverType), info.Identifier)
		} else {
			info.FragmentType = "function"
			fragmentID = fmt.Sprintf("%s_%s", fragmentIDBase, info.Identifier)
		}

		var buf bytes.Buffer
		if err := format.Node(&buf, v.fset, x); err == nil {
			sum := sha1.Sum(buf.Bytes())
			info.CodeDigest = hex.EncodeToString(sum[:])
		} else {
			fmt.Fprintf(os.Stderr, "[AST Parser] Erreur digest func/meth %s: %v\n", info.Identifier, err)
		}

		if fragmentID != "" {
			v.fragments[fragmentID] = info
		}
		return nil // Ne pas visiter le corps de la fonction/méthode

	case *ast.GenDecl:
		if x.Tok == token.TYPE {
			for _, spec := range x.Specs {
				typeSpec, ok := spec.(*ast.TypeSpec)
				if !ok || typeSpec.Name == nil || typeSpec.Name.Name == "_" || typeSpec.Type == nil {
					continue
				}
				// Créer une copie de info pour ce type spécifique
				currentTypeInfo := info
				currentTypeInfo.FragmentType = "type"
				currentTypeInfo.Identifier = typeSpec.Name.Name
				currentTypeInfo.Docstring = getDocstring(typeSpec.Doc)
				if currentTypeInfo.Docstring == "" {
					currentTypeInfo.Docstring = getDocstring(x.Doc)
				}
				currentTypeInfo.StartLine = v.fset.Position(typeSpec.Pos()).Line
				currentTypeInfo.EndLine = v.fset.Position(typeSpec.End()).Line

				// Obtenir la définition formatée du type
				tempDecl := &ast.GenDecl{Tok: token.TYPE, Specs: []ast.Spec{typeSpec}}
				formattedDef := formatNode(v.fset, tempDecl)
				if !strings.HasPrefix(formattedDef, "<!format error") {
					currentTypeInfo.Definition = strings.TrimSpace(formattedDef)
				} else {
					fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: Échec formatage déf type %s.\n", currentTypeInfo.Identifier)
					currentTypeInfo.Definition = fmt.Sprintf("type %s [définition brute non formatable]", currentTypeInfo.Identifier)
				}

				goFileNameWithoutExt := strings.TrimSuffix(filepath.Base(v.currentOriginalPathRel), ".go")
				currentFragmentID := fmt.Sprintf("%s_%s_type_%s", v.currentPackageName, goFileNameWithoutExt, currentTypeInfo.Identifier)

				var buf bytes.Buffer
				if err := format.Node(&buf, v.fset, typeSpec); err == nil {
					sum := sha1.Sum(buf.Bytes())
					currentTypeInfo.CodeDigest = hex.EncodeToString(sum[:])
				} else {
					fmt.Fprintf(os.Stderr, "[AST Parser] Erreur digest type %s: %v\n", currentTypeInfo.Identifier, err)
				}

				if currentFragmentID != "" {
					v.fragments[currentFragmentID] = currentTypeInfo
				}
			}
			return nil // Ne pas visiter les enfants du bloc de type
		}
		// On pourrait traiter token.CONST et token.VAR ici de manière similaire si besoin.
		return v

	default:
		return v
	}
}

// --- Fonctions Helper (getDocstring, extractImports, buildSignatureString, typeToString, formatNode, sanitizeIdentifier) ---
// Ces fonctions restent globalement les mêmes que dans les versions précédentes.
// sanitizeIdentifier n'a plus besoin de base64.

func getDocstring(doc *ast.CommentGroup) string {
	if doc != nil {
		return strings.TrimSpace(doc.Text())
	}
	return ""
}

func extractImports(node *ast.File) []ImportInfo {
	imports := []ImportInfo{}
	if node == nil {
		return imports
	}
	for _, imp := range node.Imports {
		var importPath, alias string
		if imp != nil && imp.Path != nil {
			importPath = strings.Trim(imp.Path.Value, `"`)
		}
		if imp != nil && imp.Name != nil {
			alias = imp.Name.Name
		}
		if importPath != "" {
			imports = append(imports, ImportInfo{Name: alias, Path: importPath})
		}
	}
	return imports
}

func buildSignatureString(fset *token.FileSet, f *ast.FuncDecl) string {
	if f == nil {
		return "<!nil FuncDecl!>"
	}
	signatureOnlyDecl := &ast.FuncDecl{Recv: f.Recv, Name: f.Name, Type: f.Type, Body: nil}
	var buf bytes.Buffer
	if err := format.Node(&buf, fset, signatureOnlyDecl); err != nil {
		fmt.Fprintf(os.Stderr, "[AST Parser] Avertissement: format.Node échec signature %s: %v\n", f.Name.Name, err)
		// Fallback très simple
		fbSig := "func "
		if f.Recv != nil && len(f.Recv.List) > 0 {
			fbSig += "(" + typeToString(fset, f.Recv.List[0].Type) + ") "
		}
		if f.Name != nil {
			fbSig += f.Name.Name
		} else {
			fbSig += "<anon>"
		}
		fbSig += "(...)" // Simplifié
		if f.Type != nil && f.Type.Results != nil && len(f.Type.Results.List) > 0 {
			fbSig += " (...)"
		}
		return fbSig
	}
	return strings.Join(strings.Fields(strings.ReplaceAll(buf.String(), "\n", " ")), " ")
}

func typeToString(fset *token.FileSet, expr ast.Expr) string {
	if expr == nil {
		return "<!nil expr!>"
	}
	return strings.TrimSpace(formatNode(fset, expr))
}

func formatNode(fset *token.FileSet, node ast.Node) string {
	if node == nil {
		return "<!nil node!>"
	}
	var buf bytes.Buffer
	if err := format.Node(&buf, fset, node); err != nil {
		if ident, ok := node.(*ast.Ident); ok {
			return ident.Name
		} // Fallback pour identifiants simples
		// Log discret pour les erreurs de formatage de nœuds internes, peut être bruyant
		// fmt.Fprintf(os.Stderr, "[AST Parser] DEBUG: Erreur format.Node pour type %T: %v\n", node, err)
		return "<!format error!>"
	}
	return buf.String()
}

func sanitizeIdentifier(s string) string {
	if s == "" {
		return "emptystr"
	}
	var result strings.Builder
	for i, r := range s {
		if unicode.IsLetter(r) || (unicode.IsDigit(r) && i > 0) { // Les identifiants ne commencent pas par un chiffre
			result.WriteRune(r)
		} else if r == '*' {
			result.WriteString("Ptr")
		} else if r == '[' && i+1 < len(s) && s[i+1] == ']' { // Gérer `[]`
			result.WriteString("Slice")
			// On pourrait avancer l'index i ici si on traitait une slice de runes, mais avec range sur string, c'est ok.
		} else if r == ']' {
			// Ignoré, géré par le `[]`
		} else if r == '.' { // Remplacer `.` par `_`
			result.WriteRune('_')
		} else if r == '_' { // Conserver les underscores
			result.WriteRune('_')
		}
		// Ignorer les autres caractères
	}
	sanitized := result.String()
	// Nettoyage des underscores multiples et de début/fin
	for strings.Contains(sanitized, "__") {
		sanitized = strings.ReplaceAll(sanitized, "__", "_")
	}
	sanitized = strings.Trim(sanitized, "_")

	if sanitized == "" { // Si tout a été strippé
		// Fallback simple basé sur un hash court si la sanitization donne une chaîne vide.
		// Alternative à base64 pour éviter l'import si pas d'autres usages.
		h := sha1.New()
		h.Write([]byte(s))
		return fmt.Sprintf("invalidident_%s", hex.EncodeToString(h.Sum(nil)[:4]))
	}
	return sanitized
}
