with Ada.Text_IO; use Ada.Text_IO;

with Langkit_Support.Text;

with Libadalang.Analysis; use Libadalang.Analysis;

procedure Main is
   Ctx    : Analysis_Context := Create;
   Unit   : Analysis_Unit := Get_From_File (Ctx, "foo.adb");
   CU     : constant Compilation_Unit := Compilation_Unit (Root (Unit));

   function Find_Binops (N : Ada_Node) return Boolean
   is (N.Kind = Ada_Bin_Op);

   BO     : constant Bin_Op :=
     Bin_Op (Ada_Node_Iterators.Consume (CU.Find (Find_Binops'Access)) (1));
begin
   Put_Line ("Tokens for node "
             & Langkit_Support.Text.Image (Short_Image (BO)) & ":");
   for Tok of BO.Token_Range loop
      Put_Line (Image (Tok));
   end loop;

   Destroy (Ctx);
end Main;
