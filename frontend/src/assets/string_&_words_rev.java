import java.util.*;

class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        String s = sc.nextLine();
        String[] words = s.split(" ");
        
        for(int i=words.length - 1;i>=0;i--){
            System.out.print(words[i]+" ");
        }
        String rev = new StringBuilder(s).reverse().toString();
        System.out.println(rev);
    }
}